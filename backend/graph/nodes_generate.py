"""生成节点: 单一生成(generator_node) / 人工审批 / 驳回重生成(regenerate_node)。

多轮上下文在这里补全: 无论走知识库还是历史, 模型都能看到最近对话与摘要,
不再出现"知识库路径丢失历史"的问题。
"""
import time

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from graph.state import MultiModalRAGState
from graph.context import get_working_window
from graph.llm_init import multiModal_llm
from graph.nodes_shared import (
    _CATEGORY_LABELS,
    _image_to_model_url,
    _extract_evidence,
)


def _build_system_prompt(summary: str, kb_context: list, image_relation: str) -> str:
    """组装生成器系统提示词: 上下文块(摘要 + 知识库检索结果) + 动态编号规则。

    kb_context 每项渲染成"检索内容N[标签]: 文本", 标签区分 [文档]/[图片]/[记忆]:
    方案B 下知识库图片以 [图片] doc 文本进上下文(模型看不到真图), 答案按编号引用。
    """
    context_blocks = []
    if summary and summary.strip():
        context_blocks.append(f"[历史对话摘要]\n{summary}")
    if kb_context:
        kb_parts = []
        for i, hit in enumerate(kb_context, 1):
            label = _CATEGORY_LABELS.get(hit.get("category", ""), "文档")
            src = f"\n资料来源: {hit.get('filename')}" if hit.get("filename") else ""
            kb_parts.append(f"检索内容{i}[{label}]: {hit.get('text', '')}{src}")
        context_blocks.append("[知识库检索结果]\n" + "\n\n".join(kb_parts))
    context_text = "\n\n".join(context_blocks)

    rule_lines = [
        "响应必须使用 Markdown 格式",
        "优先基于[知识库检索结果](含[文档]/[记忆]/[图片]来源标签)回答, 其次参考[历史对话摘要]与最近的对话消息",
        "若上下文中没有相关信息, 请直接说明并不要编造; 仅当知识库检索结果与检索到的图片均为空时, 才说明'知识库中暂无相关资料'并基于自身知识回答",
        "引用知识库内容时, 在内容末尾标注其资料来源文件名",
        "引用[图片]文档时, 在对应文字后标注其引用编号(如 [检索内容3]), 便于前端定位对应图片; 不要把图中的视觉细节当作凭空知道的内容写出来",
        "若上下文包含 HTML 表格(<table> 标签), 请转换为 Markdown 表格输出, 不要输出 HTML 标签",
    ]
    if image_relation == "contradictory":
        rule_lines.append(
            "用户文字与图片内容矛盾, 以图片真实内容为准, 礼貌指出用户的描述与图不符, 不要附和用户的错误描述"
        )
    rules = [f"{i}. {line}" for i, line in enumerate(rule_lines, 1)]

    system_prompt = "你是一名企业知识库 AI 助手, 负责回答用户的技术与业务问题。\n要求:\n" + "\n".join(rules)
    if context_text:
        system_prompt += f"\n\n以下是可用于回答的上下文:\n{context_text}"
    return system_prompt


def _build_user_content(input_text: str, input_image: str) -> list:
    """组装当前输入的用户消息块: 文本 + 用户发的图(image_url)。

    方案B: 检索图不进模型, 知识库图片以 [图片] doc 文本作答; 仅当前输入图进模型。
    """
    content: list = []
    if input_text:
        content.append({"type": "text", "text": input_text})
    url = _image_to_model_url(input_image)
    if url:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


async def generator_node(state: MultiModalRAGState, config: RunnableConfig):
    """单一生成节点: 组合 滑动窗口+摘要+知识库+记忆+当前输入, 流式生成回答。"""
    input_text = state.get("input_text") or ""
    input_image = state.get("input_image") or ""
    summary = state.get("summary") or ""
    messages = state.get("messages") or []
    kb_context = state.get("kb_context") or []

    system_prompt = _build_system_prompt(summary, kb_context, state.get("image_relation") or "")
    user_content = _build_user_content(input_text, input_image)
    # 本轮 human 消息不写入 state.messages: 由 persist_context 轮末经 append_pair 写入
    # 含 caption 富化的"历史记录"副本; 这里只构造给模型看的"实时提示"副本。
    current_human = HumanMessage(content=user_content)
    history_window = get_working_window(messages)

    memory_count = sum(1 for h in kb_context if h.get("category") == "memory")
    logger.info("[节点] generator_node: 摘要={}字符, 窗口={}条, 知识库={}条(含记忆{}), 图片={}张, 输入图={}",
                len(summary), len(history_window), len(kb_context), memory_count,
                len([h for h in kb_context if h.get("category") == "image"]), 1 if input_image else 0)

    # 历史窗口作为真实消息直传模型(含图块, 多轮"看图追问"指代自然成立),
    # 传 config: 让 LLM token 流式回传给前端(stream_mode="messages")
    response = await multiModal_llm.ainvoke(
        [SystemMessage(content=system_prompt), *history_window, current_human],
        config=config,
    )
    answer = response.content if isinstance(response.content, str) else str(response.content)
    # 方案B: 扫描回答里的 (检索内容N) 引用, 收集被引用的 doc 作为前端证据(图片+文本来源; 严格模式: 无命中为空)
    evidence = _extract_evidence(answer, kb_context)
    logger.info("[节点] generator_node 完成, 回答 {} 字符, 证据 {} 项", len(answer), len(evidence))
    return {"answer": answer, "evidence": evidence}


def human_approval(state: MultiModalRAGState):
    """人工审批中断点(interrupt_before), 本函数在恢复时才被调用。

    恢复时重置 start_ts, 让 persist_context 的 duration_ms 只统计中断后的 LLM 耗时,
    避免把人工审批等待时间算进本轮耗时。
    """
    logger.info("[节点] human_approval: human_answer={}, reason={}",
                state.get("human_answer"), state.get("human_reason"))
    return {"start_ts": time.monotonic()}


async def regenerate_node(state: MultiModalRAGState, config: RunnableConfig):
    """审批 reject 后重新生成: 复用生成器上下文(摘要+历史+知识库带标签+引用编号规则),
    叠加 草稿+驳回原因, 让模型带着完整语境改进回答; 证据同样重算。"""
    draft = state.get("answer") or ""
    human_reason = state.get("human_reason") or ""
    summary = state.get("summary") or ""
    kb_context = state.get("kb_context") or []
    messages = state.get("messages") or []
    input_text = state.get("input_text") or ""
    input_image = state.get("input_image") or ""

    # 与 generator 同一套基础提示词(摘要+检索结果+来源标签+引用编号规则+表格规则),
    # 保证重生成也按 [检索内容N] 编号引用 → 证据可重算、前端可展示
    base_prompt = _build_system_prompt(summary, kb_context, state.get("image_relation") or "")
    reject_block = (
        "\n\n[本轮回答被用户驳回]\n"
        f"驳回原因: {human_reason or '(用户未填写具体原因)'}\n"
        f"被驳回的草稿:\n{draft or '(无草稿)'}\n"
        "针对驳回原因改进回答: 不要简单重复草稿; 保留草稿中正确的部分, "
        "综合参考上下文与草稿完善。"
    )
    system_prompt = base_prompt + reject_block

    user_content = _build_user_content(input_text, input_image)
    history_window = get_working_window(messages)
    response = await multiModal_llm.ainvoke(
        [SystemMessage(content=system_prompt), *history_window, HumanMessage(content=user_content)],
        config=config,
    )
    answer = response.content if isinstance(response.content, str) else str(response.content)
    # 重生成后证据重算, 避免沿用旧草稿的引用
    evidence = _extract_evidence(answer, kb_context)
    logger.info("[节点] regenerate_node 完成, 回答 {} 字符, 证据 {} 项", len(answer), len(evidence))
    return {"answer": answer, "evidence": evidence}
