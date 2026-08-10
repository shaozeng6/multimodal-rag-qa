"""输入处理节点: 输入归一化 / 图片理解(仅纯图/图文) / 问题改写与拆分。

拓扑(workflow_service): START -> process_input
  -> (有图) image_analysis -> query_rewriter
  -> (纯文本) query_rewriter
"""
import time

from langchain_core.runnables import RunnableConfig
from loguru import logger

from graph.context import input_modality
from graph.state import MultiModalRAGState
from graph.rewrite import normalize_terms, rewrite_and_split
from graph.image_analysis import analyze_image
from graph.llm_init import multiModal_llm, rewriter_llm


def process_input(state: MultiModalRAGState, config: RunnableConfig):
    """处理用户输入: 记录日志 + 重置本轮轮级残留(审批结果 + 图片理解)。

    每轮起点统一重置, 防跨轮残留污染后续判定:
    - human_answer/human_reason: 上一轮审批结果残留会让未评估回答误写进记忆(B2)
    - image_caption/image_relation: 纯文本轮不跑 image_analysis, 不重置则上一轮
      caption/relation 残留, 生成器会误注入"图文矛盾"指令(B3)
    模态由 input_modality 派生, 仅用于日志。
    """
    user_name = config.get("configurable", {}).get("user_name", "ZS")
    input_text = state.get("input_text") or ""
    input_image = state.get("input_image") or ""
    modality = input_modality(input_text, input_image)

    # 截断 base64 图片, 避免日志过长
    if input_image:
        log_image = input_image[:60] + "..." if len(input_image) > 60 else input_image
    else:
        log_image = ""
    logger.info("[节点] process_input: 用户 {}, 模态={}, text={}, image={}",
                user_name, modality, (input_text or "")[:50], log_image)
    # 每轮重置审批结果: 上一轮 approve/reject 会随 checkpointer 残留到本轮,
    # 若不清空, 本轮 evaluate 失败(返回 None 静默放行)时会读到残留的 "approve",
    # 把未评估质量的回答写入跨用户共享的 t_context(污染检索记忆)。
    return {
        "start_ts": time.monotonic(),
        "human_answer": "",
        "human_reason": "",
        "image_caption": "",
        "image_relation": "",
        # 模态写入 state(本轮状态一部分, 供 trace/未来路由读取)
        "modality": modality,
    }


async def image_analysis_node(state: MultiModalRAGState, config: RunnableConfig):
    """图片理解: 图→caption + 图文相关性。仅在有图的轮次被路由调用。

    纯文本轮不进入本节点(workflow_service 条件边分流), 其 caption/relation 残留
    由 process_input 每轮统一清空(见其 docstring)。
    """
    input_image = state.get("input_image") or ""
    input_text = state.get("input_text") or ""
    # analyze_image 按 text 是否非空自行判断图文/纯图, 无需传 modality
    caption, relation = await analyze_image(input_image, input_text, multiModal_llm)
    return {"image_caption": caption, "image_relation": relation}


async def query_rewriter_node(state: MultiModalRAGState, config: RunnableConfig):
    """改写与拆分(按输入模态选源, 模态由 input_modality 统一判定)。

    源选择:
    - 纯图(image): source=caption(自包含, 跳过 LLM 改写, 仅术语归一化) → 文本检索"架桥"
    - 图文(text_image): 按 relation —— related 融合 caption+文字 / contradictory 只用 caption / unrelated 只用文字
    - 纯文本(text): source=text → 走原逻辑
    失败兜底: caption 空 → 纯图退化旧行为(只搜图), 图文退化只用 text。
    """
    input_text = state.get("input_text") or ""
    input_image = state.get("input_image") or ""
    image_caption = state.get("image_caption") or ""
    image_relation = state.get("image_relation") or ""
    messages = state.get("messages") or []
    modality = input_modality(input_text, input_image)

    # ① 纯图: caption 当检索源(自包含, 无需 LLM 改写)
    if modality == "image":
        if not image_caption:
            # 图片理解失败, 退化旧行为(只走图片检索)
            return {"rewritten_query": "", "sub_questions": []}
        rewritten = normalize_terms(image_caption)
        logger.info("[改写] 纯图 caption 作为检索源: {}", rewritten[:80])
        return {"rewritten_query": rewritten, "sub_questions": [rewritten]}

    # ② 图文混合(text_image): 按 relation 决定 caption 是否融合(text 非空由模态保证)
    if modality == "text_image":
        if image_relation == "related" and image_caption:
            source = f"{image_caption}\n用户问题: {input_text}"
        elif image_relation == "contradictory" and image_caption:
            source = image_caption
            logger.info("[改写] 图文矛盾, caption 单独作检索源(不融合错误文本): {}", source[:80])
        else:
            # unrelated / relation 缺失 → 只用文字(图是附件)
            source = input_text
    else:
        # ③ 纯文本
        source = input_text

    if not source.strip():
        return {"rewritten_query": "", "sub_questions": []}

    normalized = normalize_terms(source)
    if messages:
        rewritten, sub_questions = await rewrite_and_split(normalized, messages, rewriter_llm)
    else:
        rewritten, sub_questions = normalized, [normalized]
    return {"rewritten_query": rewritten, "sub_questions": sub_questions}
