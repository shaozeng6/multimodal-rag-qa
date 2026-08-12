"""问题改写与拆分。

参考 ragent 的设计:
- 一次 LLM 调用同时输出 rewrite + should_split + sub_questions(结构化输出)
- 拆分的判断规则放进 prompt 而非代码(多个问号/显式列举才拆, 抽象对比不拆)
- 改写只取最近几轮干净对话(过滤摘要)用于指代消解
- 前置一道规则化术语归一化(零 LLM 开销), 失败时兜底返回归一化后的问题
"""
import json
import os
from typing import List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field

from graph.context import format_history

# ========= 配置 =========
REWRITE_HISTORY_PAIRS = 4  # 改写参考的最近对话对数
REWRITE_TEMPERATURE = 0.1


class RewriteResult(BaseModel):
    """一次 LLM 调用同时输出改写问题与子问题。"""

    rewrite: str = Field(description="改写后完整独立的问题(指代消解、上下文补全、口语转正式)")
    should_split: bool = Field(default=False, description="是否需要拆分为子问题")
    sub_questions: List[str] = Field(default_factory=list, description="拆分出的子问题列表")


# ========= 术语归一化(规则表, 零 LLM 开销) =========
# 默认映射表; 可配置 RAG_TERM_MAPPING_PATH 指向 JSON 文件 {"别名": "标准名"}
_DEFAULT_TERM_MAPPING: dict = {
    # 示例: 按业务领域自行扩充
    # "苹果手机": "iPhone",
    # "降噪豆": "AirPods Pro",
}

_TERM_MAPPING_PATH = os.getenv("RAG_TERM_MAPPING_PATH", "")


def _load_term_mapping() -> dict:
    if not _TERM_MAPPING_PATH:
        return dict(_DEFAULT_TERM_MAPPING)
    try:
        with open(_TERM_MAPPING_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载术语映射表失败: {}, 使用内置默认表", e)
        return dict(_DEFAULT_TERM_MAPPING)


TERM_MAPPING = _load_term_mapping()


def normalize_terms(query: str) -> str:
    """规则化术语归一化: 把口语化叫法替换为标准名(纯文本替换)。"""
    if not query:
        return query
    normalized = query
    for alias, standard in TERM_MAPPING.items():
        if alias and standard and alias in normalized:
            normalized = normalized.replace(alias, standard)
    if normalized != query:
        logger.info("[改写] 术语归一化: {} -> {}", query, normalized)
    return normalized


# ========= 改写 + 拆分 =========

def _format_history_pairs(messages: list, max_pairs: int = REWRITE_HISTORY_PAIRS) -> str:
    """取最近 max_pairs 对对话用于指代消解(不包含摘要, BaseMessage 列表)。"""
    if not messages:
        return ""
    pairs = list(zip(messages[0::2], messages[1::2]))
    recent = pairs[-max_pairs:]
    return format_history([m for pair in recent for m in pair])


async def rewrite_and_split(
    query: str,
    messages: list,
    llm,
) -> Tuple[str, List[str]]:
    """改写并拆分问题。

    Args:
        query: 归一化后的用户问题(已在节点中调用 normalize_terms)
        messages: BaseMessage 对话历史(用于指代消解)
        llm: 支持结构化输出的 Fast 档 LLM

    Returns:
        (改写后的问题, 子问题列表)。LLM 失败时兜底返回 (query, [query])。
    """
    history_text = _format_history_pairs(messages)
    system_prompt = (
        "你是问题改写与拆分助手。把用户的问题改写成一条脱离对话上下文仍能独立理解、"
        "适合检索的完整问题。\n"
        "改写规则:\n"
        "1. 消除指代: 把'这个/那个/它/其风险/这种方式'等还原为具体所指\n"
        "2. 补全上下文: 缺失的主语、限定条件、时间范围补全\n"
        "3. 口语化转正式: 把口语表达转为书面、精确的检索式表达\n"
        "4. 不要回答或评价问题本身, 不要编造用户没提到的信息\n"
        "拆分规则:\n"
        "- 仅当问题包含多个独立问号或显式列举多个对象时才拆分为子问题\n"
        "- 抽象对比/关系型问题(如'有什么区别')不拆分\n"
        "- 不确定时不要拆分(should_split=false)\n"
        "按 JSON 格式返回: {\"rewrite\": \"...\", \"should_split\": true/false, \"sub_questions\": [...]}"
    )
    user_prompt = f"用户问题: {query}"
    if history_text:
        user_prompt += f"\n\n最近对话(用于消除指代):\n{history_text}"
    user_prompt += "\n\n请输出改写结果 JSON。"

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: Optional[RewriteResult] = await llm.with_structured_output(
            RewriteResult, method="json_mode"
        ).ainvoke(messages)
        if result is None or not result.rewrite.strip():
            raise ValueError("改写结果为空")
        rewrite = result.rewrite.strip()
        sub_questions = [q.strip() for q in result.sub_questions if q and q.strip()]
        if not result.should_split or not sub_questions:
            sub_questions = [rewrite]
        logger.info("[改写] 改写问题: {} -> {}; 子问题: {}", query, rewrite, sub_questions)
        return rewrite, sub_questions
    except Exception as e:
        logger.warning("问题改写失败, 使用归一化后问题兜底: {}", e)
        return query, [query]
