"""图片理解(P1): 图→caption + 图文相关性判断。

一次多模态 LLM 调用同时输出:
- caption: 图的文字描述 —— 供文本检索通道"架桥", 让纯图/图文命中文字文档
- relation: 图与用户文本的相关性("related"/"unrelated"), 决定 caption 是否融合进检索查询

健壮性: LLM 异常/解析失败一律兜底 ("", ""), 绝不中断工作流。
解析用公共 extract_json(P0-4): 兼容块式 content / markdown 围栏 / 内容含花括号,
并区分"模型无输出"与"输出无法解析"两种失败, 便于排查纯图 caption 断桥问题。
"""
from typing import Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from graph.json_utils import extract_json

# caption 生成温度(低温, 减少描述幻觉)
IMAGE_CAPTION_TEMPERATURE = 0.3

# 图文关系三态: related(文字指向图) / unrelated(图是附件) / contradictory(文字与图矛盾)
_RELATION_VALID = ("related", "unrelated", "contradictory")


def _preview(raw) -> str:
    """日志里的输出预览(截断)。"""
    return (str(raw) or "")[:200]


async def analyze_image(
    image: str,
    text: str,
    llm,
) -> Tuple[str, str]:
    """生成图片描述并判断图文相关性。

    Args:
        image: 图片 base64 data URI 或 URL
        text: 用户输入的文本(可能为空 = 纯图场景)
        llm: 多模态 LLM(multiModal_llm)

    Returns:
        (caption, relation)。relation 仅在 text 非空时才有意义:
        "related" 文本指向图 / "unrelated" 图文无关 / "contradictory" 文字与图矛盾;
        纯图或解析失败为 ""。
    """
    # 按模态拆分提示词: 纯图只问 caption(relation 无意义, 不引入三态干扰);
    # 图文才问 caption + relation 三态
    has_text = bool(text and text.strip())
    if has_text:
        system_prompt = (
            "你是一名图片理解助手。请仔细观察图片, 并输出两项内容:\n"
            "1. caption: 用 1~2 句中文客观描述图片的核心内容与关键细节, 不要推测\n"
            "2. relation: 判断图片与用户问题文本的关系:\n"
            "   - related: 用户问题指向图片内容(如'这个/这图/上面是什么'或描述图中元素)\n"
            "   - unrelated: 用户问题与图片无关(图片只是附件)\n"
            "   - contradictory: 用户问题文本对图片的描述与图片真实内容矛盾(如文字说'显示5000万'但图实际是3000万)\n"
            "按 JSON 格式返回: {\"caption\": \"...\", \"relation\": \"related\"|\"unrelated\"|\"contradictory\"}"
        )
        user_text = f"用户问题: {text}"
    else:
        system_prompt = (
            "你是一名图片理解助手。请仔细观察图片, 用 1~2 句中文客观描述图片的"
            "核心内容与关键细节, 不要推测。按 JSON 格式返回: {\"caption\": \"...\"}"
        )
        user_text = "(用户上传了一张图片, 只需生成 caption)"
    user_content: list = [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image}},
    ]
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_content)]

    try:
        response = await llm.ainvoke(messages)
        # 直接传 response.content(可能是块列表), 由 extract_json 统一处理(C3)
        data = extract_json(response.content)
        if data is None:
            # 区分"模型无输出"与"输出无法解析", 避免纯图 caption 断桥时无法定位
            if not response.content:
                logger.warning("[图片理解] 模型无输出, 兜底空 caption")
            else:
                logger.warning("[图片理解] 输出无法解析为 JSON: {}", _preview(response.content))
            return "", ""
        caption = str(data.get("caption") or "").strip()
        relation = str(data.get("relation") or "").strip().lower()
        if relation not in _RELATION_VALID:
            relation = ""
        if not text.strip():
            relation = ""  # 纯图没有"相关性"可判断
        logger.info("[图片理解] caption={}, relation={}", caption[:80] or "(空)", relation or "(纯图)")
        return caption, relation
    except Exception as e:
        logger.warning("[图片理解] 失败(不影响工作流): {}", e)
        return "", ""
