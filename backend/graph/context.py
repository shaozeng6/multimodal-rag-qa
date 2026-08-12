"""上下文管理: 标准 BaseMessage 历史 + 滑动窗口 + 滚动摘要。

设计参考 ragent(https://github.com/nageoffer/ragent) 的会话记忆实现:
- messages 只存干净的 [HumanMessage, AIMessage] 对, 不混入工具调用消息
- 保留最近 N 轮原文, 更早的内容压成滚动摘要, 控制 token 成本
- summary_anchor 水位线防止同一段内容被重复压缩
- 摘要合并用"旧摘要当 assistant 消息 + 冲突以本轮为准"避免越摘要越失真
"""
from typing import Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from services.config_service import get_int

# ========= 配置常量(集中管理; 运行时由 sys_config 覆盖, 常量仅作默认值兜底) =========
WINDOW_TURNS = 8  # 保留最近几轮原文
SUMMARY_TRIGGER_TURNS = 9  # 超过该轮数触发压缩
SUMMARY_MAX_CHARS = 400  # 摘要最大字符数
SUMMARY_MODEL_TEMPERATURE = 0.3


def _window_turns() -> int:
    return get_int("context.window_turns", WINDOW_TURNS)


def _trigger_turns() -> int:
    return get_int("context.summary_trigger_turns", SUMMARY_TRIGGER_TURNS)


def _max_chars() -> int:
    return get_int("context.summary_max_chars", SUMMARY_MAX_CHARS)


# ========= 基础操作 =========

def get_working_window(messages: list, window_turns: Optional[int] = None) -> list:
    """取最近 window_turns 对消息原文(滑动窗口, messages 为扁平 human/ai 交替)。"""
    if window_turns is None:
        window_turns = _window_turns()
    if not messages:
        return []
    pairs = list(zip(messages[0::2], messages[1::2]))
    recent = pairs[-window_turns:]
    return [msg for pair in recent for msg in pair]


def _message_to_text(msg) -> str:
    """从 BaseMessage 提取纯文本: AI 消息取 str; Human 消息取 content 里的 text 块(忽略 image_url)。

    不要用 get_buffer_string: 它会把多模态 content 的 image_url base64 原样拼进文本。
    图片轮的 caption(中间产物, 存于 additional_kwargs)在此附带给纯文本消费方,
    供改写/检索/摘要指代图内容; content 原文保持忠实不被污染。
    """
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        text = content
    else:
        parts: list = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
                # image_url 块忽略(纯文本消费方不需要图)
        text = "".join(parts)
    caption = (getattr(msg, "additional_kwargs", {}) or {}).get("caption")
    if caption:
        text = f"{text} [图片内容]: {caption}"
    return text


def _message_image_url(msg) -> Optional[str]:
    """从 BaseMessage 提取第一个 image_url 块的 url; 无图返回 None。"""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                u = block.get("image_url")
                if isinstance(u, dict):
                    return u.get("url")
    return None


def format_history(messages: list) -> str:
    """把 BaseMessage 列表格式化为文本(剥掉图块), 供改写/摘要/评估等纯文本消费方。"""
    lines = []
    for msg in messages:
        role = "用户" if getattr(msg, "type", "") == "human" else "助手"
        content = _message_to_text(msg).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def append_pair(messages: list, human_text: str, ai_text: str,
                human_image: Optional[str] = None, caption: Optional[str] = None) -> list:
    """构建本轮新增的一对标准 BaseMessage(供 add_messages reducer 追加)。

    HumanMessage 的 content 只放"用户原话 + 原图"(忠实记录); 图片描述 caption
    是中间产物, 放进 additional_kwargs 供文本腿(_message_to_text)提取, 不污染 content。
    AIMessage 为纯文本。显式 id 保证 reducer 追加确定性。
    """
    seq = len(messages) // 2
    human_content: list = [{"type": "text", "text": human_text}]
    if human_image:
        human_content.append({"type": "image_url", "image_url": {"url": human_image}})
    kwargs: dict = {"caption": caption} if caption else {}
    return [
        HumanMessage(id=f"human-{seq}", content=human_content, additional_kwargs=kwargs),
        AIMessage(id=f"ai-{seq}", content=ai_text),
    ]


def input_modality(input_text: str, input_image: str) -> str:
    """判定输入模态: 纯文本 / 纯图 / 图文混合。

    系统实际存在三种模态——纯文本、纯图(走 caption 架桥)、图文混合(走相关/矛盾判定)。
    旧二值 "only_image/has_text" 会把图文混合归并成 has_text, 丢失模态信息。
    本函数与 query_rewriter/retrieval 的模态分流口径一致, 供日志/trace(以及未来路由)使用。
    """
    has_text = bool(input_text and input_text.strip())
    has_image = bool(input_image and input_image.strip())
    if has_text and has_image:
        return "text_image"
    if has_image:
        return "image"
    return "text"


def build_human_text(input_text: Optional[str], input_image: Optional[str]) -> Tuple[str, Optional[str]]:
    """把当前轮输入表示为对话历史中的 human 文本与图片引用。

    Returns:
        (text, image): text 为对话历史文本, image 为原图引用(base64/URL)或 None。
        图文同发: (text, image)      —— 文本保留, 图引用保留(模型可还原)
        纯图:    ("[用户发送了一张图片]", image) —— 文本占位 + 图引用保留
        纯文本:  (text, None)
    """
    if input_text and input_text.strip():
        return input_text.strip(), (input_image or None)
    return ("[用户发送了一张图片]" if input_image else ""), (input_image or None)


# ========= 摘要压缩(ragent 算法) =========

def _next_compress_range(messages: list, anchor: int,
                         window_turns: Optional[int] = None) -> Optional[Tuple[int, int]]:
    """计算本次要压缩的 [start, cutoff) 区间; 无需压缩返回 None。

    采用 ragent 的半重叠策略:
    - 保留最近 window_turns 对原文, 更早的需要进摘要
    - cutoff 取"保留窗口的中点", 使摘要与窗口部分重叠, 保证叙事连续性
    - anchor(水位线) 保证同一段内容只被压缩一次
    """
    if window_turns is None:
        window_turns = _window_turns()
    length = len(messages)
    compress_until = length - window_turns  # 保留窗口的起点
    if compress_until <= 0:
        return None
    cutoff = compress_until + window_turns // 2  # 保留窗口中点
    if cutoff <= anchor:
        return None  # 水位线已覆盖, 防重复压缩
    return anchor, cutoff


def _render_summary_input(pairs: list) -> str:
    """把待摘要的对话对渲染成文本。"""
    return format_history(pairs)


async def _merge_summary(old_summary: str, pairs: list, llm, max_chars: int) -> Tuple[str, bool]:
    """用 LLM 把旧摘要与新增对话合并成新摘要。

    关键设计(取自 ragent):
    - 旧摘要作为 assistant 消息喂给 LLM, 明确"仅供合并去重, 不作为新事实来源"
    - 冲突以本轮新对话为准
    - 新摘要 ≤ max_chars 字符, 单行

    Returns:
        (新摘要, 是否成功)。失败返回 (old_summary, False), 调用方据此保持水位线
        不前进, 否则未进摘要的内容滑出窗口后会永久丢失。
    """
    system_prompt = (
        "你是一个对话记忆摘要器。请把旧的对话摘要与新增的对话内容合并, "
        "生成一份更新后的摘要。要求:\n"
        "1. 保留关键主题、决定、结论与未解决问题\n"
        "2. 丢弃重复与过时细节\n"
        "3. 旧摘要与新内容冲突时, 以新内容为准\n"
        f"4. 输出必须为单行纯文本, 不超过 {max_chars} 个字符"
    )
    messages = [SystemMessage(content=system_prompt)]
    if old_summary and old_summary.strip():
        messages.append(AIMessage(
            content=f"旧的对话摘要(仅用于合并去重, 不得将其作为新事实的来源, 冲突以本轮对话为准):\n{old_summary}"
        ))
    messages.append(HumanMessage(content=f"新增对话内容:\n{_render_summary_input(pairs)}"))
    try:
        response = await llm.ainvoke(messages)
        new_summary = response.content if isinstance(response.content, str) else str(response.content)
        new_summary = new_summary.strip().replace("\n", " ")
        if len(new_summary) > max_chars:
            new_summary = new_summary[:max_chars]
        return new_summary, True
    except Exception as e:
        logger.exception("摘要合并失败, 回退旧摘要: {}", e)
        return old_summary, False


async def compress_summary(
    messages: list,
    summary: str,
    anchor: int,
    llm,
    window_turns: Optional[int] = None,
    max_chars: Optional[int] = None,
    trigger_turns: Optional[int] = None,
) -> Tuple[str, int, bool]:
    """触发式滚动摘要压缩。

    Args:
        messages: BaseMessage 历史列表(已含本轮新增)
        summary: 旧摘要
        anchor: 水位线(上次摘要覆盖到的条数)
        llm: 摘要用 Fast 档 LLM

    Returns:
        (新摘要, 新水位线, 是否发生了压缩)
    """
    if window_turns is None:
        window_turns = _window_turns()
    if max_chars is None:
        max_chars = _max_chars()
    if trigger_turns is None:
        trigger_turns = _trigger_turns()
    turns = len(messages) // 2
    if turns < trigger_turns:
        return summary, anchor, False

    range_ = _next_compress_range(messages, anchor, window_turns)
    if range_ is None:
        return summary, anchor, False

    start, cutoff = range_
    to_summarize = messages[start:cutoff]
    new_summary, merged_ok = await _merge_summary(summary, to_summarize, llm, max_chars)
    if not merged_ok:
        # 合并失败: 水位线不前进, 下轮重试; 否则 [start:cutoff] 内容滑出窗口即永久丢失
        logger.warning("[上下文] 摘要合并失败, 水位线保持 {} 不前进, 下轮重试: {} ~ {}",
                       anchor, start, cutoff)
        return summary, anchor, False
    logger.info("[上下文] 摘要压缩: {} ~ {} (共{}对) → 新摘要{}字符, 水位线 {}",
                start, cutoff, len(to_summarize), len(new_summary), cutoff)
    return new_summary, cutoff, True
