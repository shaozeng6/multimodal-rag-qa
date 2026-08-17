"""LangGraph 工作流构建(Phase A+B/C 重构版 + P1 多模态增强)。

节点拓扑:
  START -> process_input
  -> (有图) image_analysis(图→caption+相关性) / (纯文本) query_rewriter
  -> query_rewriter(按模态选源: 纯图caption/图文related融合/图文unrelated只用text)
  -> retriever_node(统一三路) -> generator_node(当前图+检索图+历史图进模型)
  generator_node -> evaluate_node(纯图也评估, 有图增图文一致性维)
  evaluate_node --(score>=0.7)--> persist_context -> END
  evaluate_node --(score<0.7)---> human_approval(中断点)
  human_approval --(approve)----> persist_context -> END
  human_approval --(reject)-----> regenerate_node -> persist_context -> END

不再使用 tool-calling: 检索为确定性节点, 对话历史只存干净 [human, ai] 对。
checkpointer 优先 Redis(多 worker 共享状态), 连不上回退 InMemorySaver(仅开发用)。
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from loguru import logger

from core.config import settings
# 架构整理(2026-08): 去掉 nodes.py 门面, 直接引各节点模块
from graph.nodes_evaluate import evaluate_answer
from graph.nodes_generate import generator_node, human_approval, regenerate_node
from graph.nodes_input import image_analysis_node, process_input, query_rewriter_node
from graph.nodes_persist import persist_context_node
from graph.retrieval import unified_retrieve as retriever_node
from graph.routers import (
    route_human_approval_node,
    route_human_node,
    route_image_analysis,
)
from graph.state import MultiModalRAGState


def _create_checkpointer():
    """创建 checkpointer:优先 Redis(异步版),连不上则回退到内存。

    关键: graph.astream 是异步执行, 必须用 AsyncRedisSaver(异步 checkpointer)。
    RedisSaver 是同步版, 其异步方法(aget_tuple 等)会直接抛 NotImplementedError
    (langgraph-checkpoint-redis 0.5.1 与 langgraph 1.x 的已知坑)。

    注意: AsyncRedisSaver 构造是懒连接(只存连接串, 不实际建连), 构造阶段不会因
    Redis 不可用而抛错——所以这里的 try/except 只能兜 import/构造错误, 真正的
    连通性探测与降级在 init_checkpointer 的 asetup() 中完成。要求 Redis 8.0+
    或 Redis Stack(RediSearch + RedisJSON 模块)。
    """
    try:
        from langgraph.checkpoint.redis import AsyncRedisSaver

        checkpointer = AsyncRedisSaver(redis_url=settings.REDIS_URL)
        logger.info("Redis checkpointer 创建: {}", settings.REDIS_URL)
        return checkpointer
    except Exception as e:
        logger.warning("Redis checkpointer 构造失败,回退到 InMemorySaver(仅开发用): {}", e)
        return InMemorySaver()


async def init_checkpointer(graph) -> None:
    """异步初始化 checkpointer(FastAPI lifespan 中调用)。

    AsyncRedisSaver 需要 asetup() 创建搜索索引; InMemorySaver 无需初始化。
    asetup() 是真正的 Redis 连通性探测(构造是懒连接, 不会在构造阶段失败):
    一旦这里失败, 必须把 graph.checkpointer 实际替换为 InMemorySaver, 否则应用会
    照常启动但每个 chat/approve 的 checkpoint 读写都在 Redis 上运行时失败。
    """
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None:
        return
    try:
        from langgraph.checkpoint.redis import AsyncRedisSaver

        if isinstance(checkpointer, AsyncRedisSaver):
            await checkpointer.asetup()
            logger.info("Redis checkpointer 异步初始化完成")
    except Exception as e:
        logger.warning(
            "Redis checkpointer 初始化失败, 降级 InMemorySaver(仅开发用): "
            "多 worker 状态不共享, 重启即丢, 需排查 Redis 是否可用。{}", e
        )
        graph.checkpointer = InMemorySaver()


def build_graph():
    """构建并编译 LangGraph 工作流(Phase A+B)。"""
    builder = StateGraph(MultiModalRAGState)

    # 添加节点
    builder.add_node("process_input", process_input)
    builder.add_node("image_analysis", image_analysis_node)
    builder.add_node("query_rewriter", query_rewriter_node)
    builder.add_node("retriever_node", retriever_node)
    builder.add_node("generator_node", generator_node)
    builder.add_node("evaluate_node", evaluate_answer)
    builder.add_node("human_approval", human_approval)
    builder.add_node("regenerate_node", regenerate_node)
    builder.add_node("persist_context", persist_context_node)

    # 添加边
    builder.add_edge(START, "process_input")
    # 图片理解仅在有图轮次执行(纯图/图文), 纯文本直接进改写; 残留由 process_input 每轮清空
    builder.add_conditional_edges("process_input", route_image_analysis, {
        "image_analysis": "image_analysis",
        "query_rewriter": "query_rewriter",
    })
    builder.add_edge("image_analysis", "query_rewriter")
    builder.add_edge("query_rewriter", "retriever_node")
    builder.add_edge("retriever_node", "generator_node")

    # 生成完成统一进入 LLM-as-Judge 评估(含纯图输入, 防"看图回答"幻觉)。
    # 原 route_evaluate_node 恒返 evaluate_node, 条件边映射是死代码, 收敛为普通边。
    builder.add_edge("generator_node", "evaluate_node")
    builder.add_conditional_edges("evaluate_node", route_human_node, {
        "human_approval": "human_approval",
        "persist_context": "persist_context",
    })
    builder.add_conditional_edges("human_approval", route_human_approval_node, {
        "regenerate_node": "regenerate_node",
        "persist_context": "persist_context",
    })
    builder.add_edge("regenerate_node", "persist_context")
    builder.add_edge("persist_context", END)

    checkpointer = _create_checkpointer()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_approval"],  # 静态人工介入中断点, 恢复时从中断点继续
    )
    logger.info("LangGraph 工作流构建完成(Phase A+B)")
    return graph


# 全局 graph 实例
graph = build_graph()
