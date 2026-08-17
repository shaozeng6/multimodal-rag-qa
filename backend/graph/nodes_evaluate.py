"""评估节点: LLM-as-Judge(相关性 + 忠实度 + 图文一致性)。

评审模型与生成模型分离(纯文本用 judge_llm, 含图片时用多模态 multiModal_llm),
消除"自评自答"的同源偏置。评估失败(evaluate_score=None)由路由静默放行, 不打扰用户。
"""
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from graph.context import format_history, get_working_window
from graph.json_utils import extract_json
from graph.nodes_shared import (
    _CATEGORY_LABELS,
    EVAL_HISTORY_TURNS,
    MAX_IMAGES_TO_MODEL,
    _history_images_from_messages,
    _image_to_model_url,
    _retrieved_images_for_model,
)
from graph.state import MultiModalRAGState
from infra.llm import judge_llm, multiModal_llm
from services.config_service import get_int


def _collect_judge_images(state: MultiModalRAGState) -> list:
    """收集评审需看到的图(输入图 + 检索图 + 历史图), 转可加载 URL 并限量。

    方案B 下生成器只以 [图片] doc 文本作答, 但回答会引用知识库图片, judge 看不到
    就无法核实, 会把忠实描述误判为幻觉(faithfulness=2.0 案例), 故评审单独参考检索图。
    """
    images: list = []
    if state.get("input_image"):
        images.append(state["input_image"])
    images += _retrieved_images_for_model(state.get("kb_images") or [])
    images += _history_images_from_messages(state.get("messages") or [])
    max_images = get_int("rag.max_images_to_model", MAX_IMAGES_TO_MODEL)
    return [u for u in (_image_to_model_url(i) for i in images) if u][:max_images]


def _build_judge_prompt(state: MultiModalRAGState, images: list) -> str:
    """组装评审提示词: 用户输入 + 对话/检索上下文 + 三维度评分规则 + 待评回答。"""
    input_text = state.get("input_text") or ""
    rewritten_query = state.get("rewritten_query") or ""
    summary = state.get("summary") or ""
    messages = state.get("messages") or []
    kb_context = state.get("kb_context") or []
    answer = state.get("answer") or ""
    has_image = bool(images)

    # ---- 对话上下文(摘要 + 最近窗口, 限量) ----
    dialog_parts = []
    if summary and summary.strip():
        dialog_parts.append(f"[历史对话摘要]\n{summary}")
    eval_turns = get_int("evaluate.history_turns", EVAL_HISTORY_TURNS)
    recent = get_working_window(messages, window_turns=eval_turns)
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

    # ---- 三维度评分规则 ----
    # 忠实度 grounding 来源: 检索 + 对话上下文; 有图时图片内容也算(对图描述以图为准)
    faithfulness_dim = """2. **忠实度**: 判断回答是否忠于【检索上下文】(首要依据), 对话上下文仅作次要参考。
   - 回答应主要基于知识库检索结果作答, 不得编造其中不存在的信息。
   - 仅当回答明确延续之前对话(如"如前所述/上文提到")时, 对话上下文才可作为依据; 不能以"对话里说过"为编造辩护。
   - 检索为空时: 系统明确说明"知识库暂无相关资料"后基于自身知识回答, 不算编造(诚实拒答的延伸)。
   - 编造/幻觉内容 → 低分。
"""
    if has_image:
        faithfulness_dim = """2. **忠实度**: 判断回答是否忠于【检索上下文】与【图片内容】(首要依据), 对话上下文仅作次要参考。
   - 回答应主要基于知识库检索结果与真实图片作答, 不得编造其中不存在的信息。
   - 对图中内容的描述以图片为准; 若用户文字与图片矛盾, 回答以图为据纠正用户 → 忠实表现。
   - 仅当回答明确延续之前对话(如"如前所述/上文提到")时, 对话上下文才可作为依据; 不能以"对话里说过"为编造辩护。
   - 检索为空时: 系统明确说明"知识库暂无相关资料"后基于自身知识回答, 不算编造(诚实拒答的延伸)。
   - 编造/幻觉内容 → 低分。
"""
    # 有图时增加"图文一致性"维度(反图片幻觉: 回答对图的描述必须忠于图真实内容)
    # 关键: 回答"不涉及图片" ≠ 图文不一致, 应给 10 分(无风险), 否则 min() 木桶效应会把
    # 好的纯文本回答误判低分拉进人工审批(知识蒸馏案例: relevance/faithfulness=10, image_fidelity=5 → 总分 0.5)。
    extra_dim = """3. **图文一致性**: 判断回答中涉及图片的描述是否与图片真实内容一致。
   - **若回答完全不涉及任何图片内容**(未描述任何图、未引用图中信息), 本维度给 **10 分** ——
     不存在图文不一致风险, "未描述图"不构成扣分理由。
   - 回答对"用户输入图"的描述: 逐点与图片内容核对, 一致 → 高分; 编造图中不存在的细节/数字/关系 → 低分。
   - 回答对"知识库引用图"的描述: 系统只向生成器提供了该图的文字描述、未提供原图,
     回答基于文字描述作答是合理行为; 只要不与图片实际内容明显冲突, 不视为幻觉。
   - 若图片无法加载/内容不可辨, 以回答与检索上下文中该图文字描述的一致性为准, 不因"看不清图"扣分。
   - 编造"图中与文字描述中均不存在"的细节/数字/关系 → 低分。
"""
    extra_section = f"\n{extra_dim}" if has_image else ""
    json_schema = ('{"relevance": <0-10>, "faithfulness": <0-10>, "image_fidelity": <0-10>}'
                   if has_image else '{"relevance": <0-10>, "faithfulness": <0-10>}')

    return f"""你是一名专业的 RAG 系统评估专家。请对以下 RAG 系统的回答进行评估。

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

1. **相关性**: 判断回答是否恰当回应了【系统改写后的查询】(该查询已结合对话历史消解指代、补全上下文, 是自包含的独立问题)。
   - 直接据此判断回答是否正面回应; 若改写明显偏离用户原始意图(理解歪了), 以原始问题为准并判低相关。
   - 对开放式问题("这是什么/什么意思/解释一下XX/看图说明"等), 只要回答正面解释主题、覆盖用户想知道的, 即为高相关; 回答详尽、专业、有结构都不是扣分理由。
   - 若检索上下文与问题相关: 回答应正面回应, 答非所问或遗漏关键信息 → 低分; 完整回应 → 高分。
   - 若检索上下文不相关或为空: 系统应诚实拒答(说明无法回答/知识库无相关内容)。正确拒答 → 高分; 强行编造 → 低分。

{faithfulness_dim}
{extra_section}
请严格按以下 JSON 格式返回(不要输出其他内容):
{json_schema}"""


def _preview(raw) -> str:
    """评审/图片理解日志里的输出预览(截断)。"""
    return (str(raw) or "")[:200]


def _parse_judge_score(raw, has_image: bool) -> Optional[float]:
    """解析评审 JSON, 三维度取 min(木桶效应)。

    解析失败返回 None(由路由静默放行, 区别于"答得差"的低分): judge 输出格式
    抖动(代码块/花括号被回显、块式结构、围栏包裹)是基础设施问题, 不该把好回答
    误判进人工审批/审核队列(B5/B6 修复, 原实现解析失败回落 0.5)。
    """
    # 维分默认值从 sys_config 读(hot 生效), 常量作默认值兜底
    dim_default = get_int("evaluate.dim_default", 5)
    image_fidelity_default = get_int("evaluate.image_fidelity_default", 10)
    data = extract_json(raw)
    if data is None:
        logger.warning("LLM Judge 评分解析失败(静默放行, 区别于低分), 原始输出: {}", _preview(raw))
        return None
    try:
        relevance = float(data.get("relevance", dim_default)) / 10.0
        faithfulness = float(data.get("faithfulness", dim_default)) / 10.0
        # 有图时读取图文一致性维度; 缺省给 10(视为无图文不一致风险: 回答未涉及图或已核对),
        # 避免旧模型漏输出该字段时默认 5 被 min() 木桶效应误判低分(知识蒸馏案例根因)
        image_fidelity = (float(data.get("image_fidelity", image_fidelity_default)) / 10.0) if has_image else 1.0
    except (TypeError, ValueError) as e:
        logger.warning("LLM Judge 评分字段非法(静默放行): {}, 原始输出: {}", e, _preview(raw))
        return None

    if has_image:
        logger.info("LLM Judge 评分 - relevance: {:.1f}, faithfulness: {:.1f}, image_fidelity: {:.1f}",
                    relevance * 10, faithfulness * 10, image_fidelity * 10)
        return min(relevance, faithfulness, image_fidelity)
    logger.info("LLM Judge 评分 - relevance: {:.1f}, faithfulness: {:.1f}",
                relevance * 10, faithfulness * 10)
    # 木桶效应: 任一维度差则整体分数低
    return min(relevance, faithfulness)


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

        judge_images = _collect_judge_images(state)
        has_judge_image = bool(judge_images)
        answer_preview = answer[:80] + "..." if len(answer) > 80 else answer
        logger.info("[节点] evaluate_answer 开始: 有文本={}, 有图片={}, context={} 条, 回答预览={}",
                    bool(state.get("input_text")), has_judge_image,
                    len(state.get("kb_context") or []), answer_preview)

        # 检索图可观测: 打印检索到的图片路径 + 图文 context 描述, 便于定位 judge 依据哪张图
        img_ctx = [
            f"{h.get('filename') or '?'}: {(h.get('text') or '')[:80]}"
            for h in (state.get("kb_context") or []) if h.get("category") == "image"
        ]
        logger.info("[评估] 检索图 {} 张(kb_images): {}{}",
                    len(state.get("kb_images") or []),
                    (state.get("kb_images") or [])[:10],
                    f"; 图文描述: {img_ctx}" if img_ctx else "")

        # 有图时评审需多模态(用 multiModal_llm), 纯文本用独立 judge_llm(消除自评偏置)
        judge = multiModal_llm if has_judge_image else judge_llm
        prompt = _build_judge_prompt(state, judge_images)

        # 评审消息 = 提示词 + 图片(评审需看到回答引用的图)
        user_content: list = [{"type": "text", "text": prompt}]
        for url in judge_images:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
        response = await judge.ainvoke([HumanMessage(content=user_content)], config=config)
        # 直接传 response.content(可能是块列表), 由 extract_json 统一处理
        score = _parse_judge_score(response.content, has_judge_image)
        if score is None:
            # 解析失败 ≠ 低分: 按"评估失败"处理, 路由静默放行, 不打扰用户
            logger.warning("[节点] evaluate_answer: 评审输出无法解析, 返回 None 静默放行")
            return {"evaluate_score": None}
        logger.info("[节点] evaluate_answer 完成: LLM Judge 分数={:.3f}", score)
        return {"evaluate_score": float(score)}
    except Exception as e:
        logger.warning("评估异常(区别于低分), 返回 None 静默放行: {}", e)
        return {"evaluate_score": None}
