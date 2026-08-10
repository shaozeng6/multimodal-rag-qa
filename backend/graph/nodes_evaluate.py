"""评估节点: LLM-as-Judge(相关性 + 忠实度 + 图文一致性)。

评审模型与生成模型分离(纯文本用 judge_llm, 含图片时用多模态 multiModal_llm),
消除"自评自答"的同源偏置。评估失败(evaluate_score=None)由路由静默放行, 不打扰用户。
"""
import json
import re
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from graph.state import MultiModalRAGState
from graph.context import get_working_window, format_history
from graph.llm_init import judge_llm, multiModal_llm
from graph.nodes_shared import (
    _image_to_model_url,
    _retrieved_images_for_model,
    _history_images_from_messages,
    _CATEGORY_LABELS,
    MAX_IMAGES_TO_MODEL,
    EVAL_HISTORY_TURNS,
)


async def evaluate_answer(state: MultiModalRAGState, config: RunnableConfig):
    """评估大模型的响应质量(LLM as Judge), 读 state["answer"]。

    Phase C:
    - 评估失败(LLM 异常)返回 evaluate_score=None, 与"低分"区分:
      None 表示基础设施问题, 由路由静默放行, 不打扰用户走人工审批
    - 评审模型与生成模型分离(纯文本用 judge_llm, 含图片时用多模态 multiModal_llm)
    """
    try:
        answer = state.get("answer") or ""
        if not answer:
            logger.warning("无回答可评估, 返回 None(静默放行)")
            return {"evaluate_score": None}

        context_retrieved = state.get("kb_context") or []
        input_text = state.get("input_text")
        answer_preview = answer[:80] + "..." if len(answer) > 80 else answer

        # 评审需看到回答引用的全部图(输入图 + 检索图 + 历史图): 方案B 下生成器只以
        # [图片] doc 文本作答, 但回答仍会引用知识库图片, judge 看不到就无法核实,
        # 会把忠实描述误判为幻觉(faithfulness=2.0 案例), 故评审单独参考检索图。
        judge_images: list = []
        if state.get("input_image"):
            judge_images.append(state["input_image"])
        judge_images += _retrieved_images_for_model(state.get("kb_images") or [])
        judge_images += _history_images_from_messages(state.get("messages") or [])
        judge_images = [
            u for u in (_image_to_model_url(i) for i in judge_images) if u
        ][:MAX_IMAGES_TO_MODEL]
        has_judge_image = bool(judge_images)

        logger.info("[节点] evaluate_answer 开始: 有文本={}, 有图片={}, context={} 条, 回答预览={}",
                    bool(input_text), has_judge_image, len(context_retrieved), answer_preview)

        # 有图时评审需多模态(用 multiModal_llm), 纯文本用独立 judge_llm(消除自评偏置)
        judge = multiModal_llm if has_judge_image else judge_llm
        score = await _evaluate_with_llm_judge(state, config, judge, images=judge_images)
        logger.info("[节点] evaluate_answer 完成: LLM Judge 分数={:.3f}", score)
        return {"evaluate_score": float(score)}
    except Exception as e:
        logger.warning("评估异常(区别于低分), 返回 None 静默放行: {}", e)
        return {"evaluate_score": None}


async def _evaluate_with_llm_judge(
    state: MultiModalRAGState,
    config: RunnableConfig,
    judge,
    images: Optional[list] = None,
) -> float:
    """LLM as Judge: 综合评估回答质量(相关性 + 忠实度 + 图文一致性)。

    Eval 增强(对齐行业做法):
    - grounding set = 生成器实际看到的(摘要 + 最近窗口 + 记忆 + kb_context + 全部图片), 限量
    - 相关性维度看"原始问题 + 系统改写"(抓改写错误导致的答非所问)
    - 多轮指代靠"对话上下文"消解
    - 检索为空时诚实拒答/说明后基于自身知识不算编造
    - images: 回答引用的全部图(输入图 + 检索图 + 历史图)。生成器方案B 只以文本作答,
      但评审仍需看到检索图/历史图来核实回答中的图片引用, 否则会把忠实描述误判为幻觉
      (faithfulness=2.0 案例)
    """
    input_text = state.get("input_text") or ""
    rewritten_query = state.get("rewritten_query") or ""
    summary = state.get("summary") or ""
    messages = state.get("messages") or []
    kb_context = state.get("kb_context") or []
    answer = state.get("answer") or ""
    images = images or []
    has_image = bool(images)

    # ---- 对话上下文(摘要 + 最近窗口, 限量) ----
    dialog_parts = []
    if summary and summary.strip():
        dialog_parts.append(f"[历史对话摘要]\n{summary}")
    recent = get_working_window(messages, window_turns=EVAL_HISTORY_TURNS)
    if recent:
        dialog_parts.append(f"[最近对话]\n{format_history(recent)}")
    dialog_text = "\n\n".join(dialog_parts) or "(无对话历史)"

    # ---- 检索上下文(带来源标签; 记忆命中已并入 kb_context) ----
    if kb_context:
        ctx_parts = []
        for i, hit in enumerate(kb_context, 1):
            label = _CATEGORY_LABELS.get(hit.get("category", ""), "文档")
            src = f"\n资料来源: {hit.get('filename')}" if hit.get("filename") else ""
            ctx_parts.append(f"[{i}][{label}] {hit.get('text', '')}{src}")
        context_text = "\n".join(ctx_parts)
        retrieval_state = f"检索到 {len(kb_context)} 条上下文"
    else:
        context_text = "(检索为空)"
        retrieval_state = "检索为空(知识库未检索到相关内容)"

    has_image_desc = f"含 {len(images)} 张图(下方: 用户输入/检索/历史图)" if has_image else "无图片"
    # 有图时增加"图文一致性"维度(反图片幻觉: 回答对图的描述必须忠于图真实内容)
    extra_dim = """3. **图文一致性**: 判断回答中涉及图片的描述是否与图片真实内容一致。
   - 回答对"用户输入图"的描述应逐点与图片内容核对, 一致 → 高分。
   - 回答对"知识库引用图"的描述: 系统只向生成器提供了该图的文字描述、未提供原图,
     回答基于文字描述作答是合理行为; 只要不与图片实际内容明显冲突, 不视为幻觉。
   - 编造"图中与文字描述中均不存在"的细节/数字/关系 → 低分。
   - 若回答不涉及图片内容, 本维度给 5 分(中性)。
"""
    # 忠实度 grounding 来源: 检索 + 对话上下文; 有图时图片内容也算(对图描述以图为准)
    faithfulness_dim = """2. **忠实度**: 判断回答是否忠于【检索上下文】与【对话上下文】(回答可依据的来源)。
   - 回答内容应基于以上任一来源, 不得编造其中不存在的信息。
   - 检索为空时: 系统明确说明"知识库暂无相关资料"后基于自身知识回答, 不算编造(诚实拒答的延伸)。
   - 编造/幻觉内容 → 低分。
"""
    if has_image:
        faithfulness_dim = """2. **忠实度**: 判断回答是否忠于【检索上下文】【对话上下文】或【图片内容】(回答可依据的来源)。
   - 回答内容应基于以上任一来源, 不得编造其中不存在的信息。
   - 对图中内容的描述以图片为准; 若用户文字与图片矛盾, 回答以图为据纠正用户 → 忠实表现。
   - 检索为空时: 系统明确说明"知识库暂无相关资料"后基于自身知识回答, 不算编造(诚实拒答的延伸)。
   - 编造/幻觉内容 → 低分。
"""
    extra_section = f"\n{extra_dim}" if has_image else ""
    json_schema = ('{"relevance": <0-10>, "faithfulness": <0-10>, "image_fidelity": <0-10>}'
                   if has_image else '{"relevance": <0-10>, "faithfulness": <0-10>}')
    judge_prompt = f"""你是一名专业的 RAG 系统评估专家。请对以下 RAG 系统的回答进行评估。

## 用户输入
- 原始问题: {input_text or "(无文本)"}
- 系统改写: {rewritten_query or "(无改写, 纯图输入)"}
- 图片: {has_image_desc}
- 检索状态: {retrieval_state}

## 对话上下文
{dialog_text}

## 检索上下文
{context_text}

## 系统回答
{answer}

## 评估要求
请从以下维度评估, 每个维度打 0-10 分(整数):

1. **相关性**: 判断回答是否恰当回应了用户的【原始问题】。
   - 对开放式问题("这是什么/什么意思/解释一下XX/看图说明"等), 只要回答正面解释主题、覆盖用户想知道的, 即为高相关; 回答详尽、专业、有结构都不是扣分理由。
   - "系统改写"只是系统对问题的规范化理解, 不应作为低分依据: 仅当回答整体偏离用户原始意图(答非所问)才低分; 不要因改写更具体、措辞与原文不同而扣分。
   - 结合"对话上下文"理解多轮指代("那/这个"等指向之前讨论的内容)。
   - 若检索上下文与问题相关: 回答应正面回应, 答非所问或遗漏关键信息 → 低分; 完整回应 → 高分。
   - 若检索上下文不相关或为空: 系统应诚实拒答(说明无法回答/知识库无相关内容)。正确拒答 → 高分; 强行编造 → 低分。

{faithfulness_dim}
{extra_section}
请严格按以下 JSON 格式返回(不要输出其他内容):
{json_schema}"""

    user_content: list = [{"type": "text", "text": judge_prompt}]
    for url in images:
        user_content.append({"type": "image_url", "image_url": {"url": url}})
    message = HumanMessage(content=user_content)

    response = await judge.ainvoke([message], config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)

    try:
        json_match = re.search(r'\{[^}]+\}', raw)
        if json_match:
            scores = json.loads(json_match.group())
            relevance = scores.get('relevance', 5) / 10.0
            faithfulness = scores.get('faithfulness', 5) / 10.0
            # 有图时读取图文一致性维度; 缺省给中性分(向后兼容旧模型输出)
            image_fidelity = (scores.get('image_fidelity', 5) / 10.0) if has_image else 1.0
        else:
            logger.warning("LLM Judge 返回格式异常, 原始输出: {}", raw[:200])
            return 0.5
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("LLM Judge 评分解析失败: {}, 原始输出: {}", e, raw[:200])
        return 0.5

    if has_image:
        logger.info("LLM Judge 评分 - relevance: {:.1f}, faithfulness: {:.1f}, image_fidelity: {:.1f}",
                    relevance * 10, faithfulness * 10, image_fidelity * 10)
        return min(relevance, faithfulness, image_fidelity)
    logger.info("LLM Judge 评分 - relevance: {:.1f}, faithfulness: {:.1f}",
                relevance * 10, faithfulness * 10)
    # 木桶效应: 任一维度差则整体分数低
    return min(relevance, faithfulness)
