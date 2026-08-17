"""LLM 实例工厂与全局实例(从 graph/llm_init.py 拆分, 2026-08 架构整理)。

统一工厂收敛, 消除四处分散的 ChatOpenAI 构造; 实例参数与原实现逐一吻合。
"""
from typing import Optional

from langchain_openai import ChatOpenAI

from infra.config import (
    EVAL_LLM_MODEL,
    JUDGE_LLM_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MULTIMODAL_LLM_MODEL,
)


def _make_llm(model: str, *, temperature: float = 0.2, streaming: bool = True,
              enable_thinking: Optional[bool] = None) -> ChatOpenAI:
    """按配置创建 ChatOpenAI 实例。

    Args:
        model: 模型名
        temperature: 采样温度
        streaming: 是否流式(生成节点需要, 改写/摘要/评审关闭)
        enable_thinking: 是否开启 thinking; None 表示不传 extra_body(保持模型默认)
    """
    kwargs = dict(
        model=model,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        temperature=temperature,
        streaming=streaming,
    )
    if enable_thinking is not None:
        kwargs["extra_body"] = {"enable_thinking": enable_thinking}
    return ChatOpenAI(**kwargs)


# 主对话 LLM(流式, 生成回答; 供 ingestion/convert 的表格描述等通用文本任务)
# 温度属基础设施配置, 固定默认值
llm = _make_llm(LLM_MODEL, temperature=0.2, streaming=True)

# 多模态大模型(对话/图片理解/重生成; 保持原默认温度 0.7 不变)
multiModal_llm = _make_llm(MULTIMODAL_LLM_MODEL, temperature=0.7, streaming=True)

# 改写/摘要专用 LLM(Fast 档: 不流式, 低温, 关 thinking; 供 query_rewriter 与摘要压缩)
rewriter_llm = _make_llm(EVAL_LLM_MODEL, temperature=0.1, streaming=False, enable_thinking=False)

# 评审 LLM(LLM as Judge, 用于纯文本回答的评估)
# 独立于生成模型(qwen3-vl-plus), 消除"自评自答"的同源偏置。
# 生产环境建议配置为与生成模型不同系列/不同供应商的模型。
# 注意: 有图片输入时评审仍需多模态, 走 multiModal_llm(见 nodes_evaluate 的选取逻辑)。
judge_llm = _make_llm(JUDGE_LLM_MODEL, temperature=0, streaming=False, enable_thinking=False)
