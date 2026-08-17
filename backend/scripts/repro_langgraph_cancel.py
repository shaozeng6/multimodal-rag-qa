"""最小复现: LangGraph 流被中途取消(cancellation)后的 checkpoint 语义(P0-3)。

背景: SSE 客户端断开时, FastAPI StreamingResponse 会取消 event_stream 生成器,
graph.astream 在任意 await 点被掐断。本脚本验证两个问题:
1. 取消后线程 checkpoint 的 next 是否残留未完成节点(即"半截运行"是否存在)?
2. 同一线程再 astream(新输入) 是否会把新输入并进旧残留分支(状态污染)?
3. 用 astream(None) 先收尾, 再发新消息, 是否得到干净状态?

用法(在项目实际 Python 环境, backend 目录):
    python -m scripts.repro_langgraph_cancel

预期输出(对应三组场景):
- 场景A: 取消后 next 含未完成节点 → 证实"残留运行"前提
- 场景B(危险路径): 直接 astream(新输入) 会看到新输入被旧分支吞掉/混合
- 场景C(修复路径): 先 astream(None) 收尾 → 新一轮干净开始
"""
import asyncio
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph


class _State(TypedDict, total=False):
    input_text: str
    step: int


async def _slow_node(state: _State) -> dict:
    """模拟 LLM 生成节点: 分两段 sleep, 中途取消时正处于本节点内。"""
    await asyncio.sleep(0.5)  # 第一段(取消点大概率落在这里)
    await asyncio.sleep(0.5)
    return {"step": (state.get("step") or 0) + 1}


async def _tail_node(state: _State) -> dict:
    """模拟 persist 节点: 输出收到的 input_text, 便于观察输入是否被污染。"""
    return {"step": (state.get("step") or 0) + 1}


def build_graph():
    builder = StateGraph(_State)
    builder.add_node("slow_node", _slow_node)
    builder.add_node("tail_node", _tail_node)
    builder.add_edge(START, "slow_node")
    builder.add_edge("slow_node", "tail_node")
    builder.add_edge("tail_node", END)
    return builder.compile(checkpointer=InMemorySaver())


async def drain(gen):
    """消费完一个异步生成器(模拟前端正常收完流)。"""
    async for _ in gen:
        pass


async def main():
    graph = build_graph()
    thread = "t1"
    config = {"configurable": {"thread_id": thread}}

    # ---------- 场景 A: 中途取消, 检查残留 ----------
    print("=" * 60)
    print("场景 A: 流中途取消后, checkpoint 的 next 是否残留")
    run = graph.astream({"input_text": "旧问题"}, config, stream_mode="updates")
    first = await run.__anext__()          # slow_node 开始(sleep 中)
    print("  已开始执行, 首步产出:", first)
    await run.aclose()                     # 模拟 SSE 断开: 关闭生成器
    state = await graph.aget_state(config)
    print("  取消后 next =", state.next)
    print("  取消后 values =", state.values)
    assert state.next, "取消后 next 为空? 那本场景不成立, 请检查 langgraph 版本行为"

    # ---------- 场景 B(危险路径): 不收尾, 直接新输入 ----------
    print("=" * 60)
    print("场景 B: 不先收尾, 直接 astream(新输入)")
    try:
        await drain(graph.astream({"input_text": "新问题"}, config, stream_mode="updates"))
    except Exception as e:
        print("  直接新输入抛异常:", type(e).__name__, e)
    state = await graph.aget_state(config)
    print("  结束后 next =", state.next, " values =", state.values)
    print("  ↑ 若 values 里 input_text 被旧分支覆盖/混合, 即证实状态污染")

    # ---------- 场景 C(修复路径): 先 astream(None) 收尾 ----------
    print("=" * 60)
    print("场景 C: 先 astream(None) 收尾残留, 再发新消息")
    await drain(graph.astream(None, config, stream_mode="updates"))  # 收尾
    state = await graph.aget_state(config)
    print("  收尾后 next =", state.next)
    await drain(graph.astream({"input_text": "真正的新问题"}, config, stream_mode="updates"))
    state = await graph.aget_state(config)
    print("  新一轮结束后 next =", state.next, " values =", state.values)
    print("  ↑ 新一轮 input_text 应保持为'真正的新问题', 未被旧分支污染")

    print("=" * 60)
    print("完成。若场景 A next 非空、场景 B 出现污染、场景 C 干净, 则后端" 
          "「新 chat 前先 _finish_abandoned_run 收尾」的修复方向正确。")


if __name__ == "__main__":
    asyncio.run(main())
