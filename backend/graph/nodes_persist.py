"""持久化节点: 对话历史 + 摘要压缩 + MySQL 消息 + Milvus 检索记忆。

仅在正常结束 / 审批通过 / 驳回重生成后执行(被中断时不执行)。
MySQL 保存完整历史; Milvus 只写"通过/高分"的回答(质量线由 _should_memoize 判定)。
"""
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig
from loguru import logger

from core.config import settings
from graph.context import (
    append_pair,
    build_human_text,
    compress_summary,
    input_modality,
)
from graph.llm_init import rewriter_llm
from graph.milvus_writer import get_milvus_writer
from graph.state import MultiModalRAGState
from services.config_service import get_float


def _build_trace(state: MultiModalRAGState, duration_ms: Optional[int]) -> dict:
    """组装本轮中间过程 trace(等效 ragent t_message 的中间证据列)。

    刻意不存检索全文(省空间), 只存结构化摘要: 命中文件名/类别/分数。
    只随 ai 消息落库供审计/调优, 永不回流为模型上下文(读写分离)。
    """
    kb_context = state.get("kb_context") or []
    return {
        "input_text": (state.get("input_text") or "")[:200],
        # 优先读 state 里的 modality(process_input 已写); 兼容旧 checkpoint 缺失时现算
        "modality": (state.get("modality")
                     or input_modality(state.get("input_text") or "", state.get("input_image") or "")),
        "image_caption": (state.get("image_caption") or "")[:200],
        "image_relation": state.get("image_relation") or "",
        "rewritten_query": (state.get("rewritten_query") or "")[:300],
        "sub_questions": state.get("sub_questions") or [],
        "kb_context": [
            {"filename": h.get("filename"), "category": h.get("category"),
             "score": round(float(h.get("score", 0)), 4)}
            for h in kb_context
        ],
        "kb_images": (state.get("kb_images") or [])[:5],
        "retrieval_ok": bool(state.get("retrieval_ok")),
        "evaluate_score": state.get("evaluate_score"),
        "human_answer": state.get("human_answer") or "",
        "duration_ms": duration_ms,
        # 引用证据(原始 image_path, 供历史回放时 resolve 成 URL)
        "evidence": state.get("evidence") or [],
    }


def _should_memoize(state: MultiModalRAGState) -> bool:
    """判断该轮 AI 回答是否应写入 Milvus 检索记忆。

    规则(Phase C):
    - 人工明确 approve → 写
    - 评估分 >= 阈值 → 写
    - reject 路径(重生成回答未再评分) → 不写
    - 评估失败(None) → 不写(无法证明质量)
    """
    if state.get("human_answer") == "approve":
        return True
    score = state.get("evaluate_score")
    if score is None:
        return False
    # 阈值从 sys_config 读(hot 生效), settings.EVALUATE_THRESHOLD 作兜底
    threshold = get_float("evaluate.threshold", settings.EVALUATE_THRESHOLD)
    return float(score) >= threshold


async def persist_context_node(state: MultiModalRAGState, config: RunnableConfig):
    """轮末持久化: 追加干净[human,ai]对 → 触发摘要压缩 → MySQL → Milvus。

    仅在正常结束 / 审批通过 / 驳回重生成后执行(被中断时不执行)。
    """
    answer = state.get("answer") or ""
    session_id = state.get("session_id") or ""
    user = state.get("user") or "unknown"
    if not answer:
        logger.warning("持久化跳过: 无回答")
        return {}

    # 1. 追加本轮 [HumanMessage, AIMessage] 对(add_messages reducer 自动累计); 图片以
    #    image_url 块写入 human 消息。摘要压缩读完整历史, 但只把新增对返回给 reducer。
    human_text, human_image = build_human_text(state.get("input_text"), state.get("input_image"))
    # P1: 图片轮的 caption 放 additional_kwargs(由 _message_to_text 供文本腿提取),
    # 不拼进 human 文本, 保持 messages 忠实记录用户原话。
    history = state.get("messages") or []
    new_pair = append_pair(history, human_text, answer,
                           human_image=human_image, caption=state.get("image_caption") or "")
    full_messages = [*history, *new_pair]
    summary, anchor, compressed = await compress_summary(
        full_messages, state.get("summary") or "", state.get("summary_anchor") or 0, rewriter_llm
    )

    # 2. MySQL 消息持久化(AI 回答; human 消息已在 chat.py 流开始前保存)
    #    schema_v2: 图片引用 → message_images, 中间过程 trace → message_traces
    #    (等效 ragent t_message 的中间证据列, 只写不回流)
    # 方案B: 持久化证据图(被引用的图片 doc), 供历史回放还原"引用证据"区; 文本来源不入 message_images
    images = [e.get("image_path") for e in (state.get("evidence") or []) if e.get("image_path")]
    start_ts = state.get("start_ts") or 0.0
    duration_ms = int((time.monotonic() - start_ts) * 1000) if start_ts else None
    trace = _build_trace(state, duration_ms)
    try:
        from services.message_service import save_message

        await save_message(session_id, "ai", answer,
                           images=images, image_type="retrieved", trace=trace)
    except Exception as e:
        logger.exception("保存 AI 消息失败: {}", e)

    # 3. Milvus 上下文持久化(供后续跨会话检索)
    #    Phase C: 只把"通过/高分"的回答写入检索记忆, 避免低质量/被驳回的
    #    回答进入上下文库造成错误累积污染。MySQL 仍保存完整历史。
    should_memoize = _should_memoize(state)
    if should_memoize:
        try:
            writer = get_milvus_writer()
            await writer.async_insert(answer, user, "AIMessage")
        except Exception as e:
            logger.exception("写入 Milvus 上下文失败: {}", e)
    else:
        logger.info("[节点] persist_context: 回答未达质量线, 跳过 Milvus 上下文写入")

    logger.info("[节点] persist_context 完成: messages={}条, summary={}字符, 压缩={}",
                len(full_messages), len(summary), compressed)
    return {
        # add_messages reducer 只追加新增对, 不返回全量
        "messages": new_pair,
        "summary": summary,
        "summary_anchor": anchor,
    }
