"""graph 节点共享工具: 图片进模型辅助 + 引用证据提取 + 常量。

方案B 后生成器不再把检索图注入模型视野(知识库图片以 [图片] 标签 doc 文本进上下文,
答案按 检索内容N 引用, 对应图片由前端证据区展示); 仅当前输入图进用户消息,
历史图随 messages 直传。评审节点需与生成器看到的口径一致, 故图集与证据逻辑
抽到本模块供 generator/evaluate/regenerate 复用。
"""
import os
import re
from typing import Optional

from graph.context import get_working_window, _message_image_url
from graph.llm_init import image_to_base64


# ========= 图片进模型: 上限控制, 控 token =========
RETRIEVED_IMAGES_TO_MODEL = 2  # 检索命中的图, 评审最多参考几张(仅 evaluate 核实用)
HISTORY_IMAGES_TO_MODEL = 1    # 历史对话里的图, 最多还原几张(judge 用)
MAX_IMAGES_TO_MODEL = 4        # 当前输入图 + 历史图 总上限

# ========= 引用证据(方案B): 从回答扫描 (检索内容N) 定位被引用的图片 doc =========
_CITE_RE = re.compile(r"检索内容(\d+)")

# ========= 评估 grounding set(Eval 增强): judge 看到生成器看到的 =========
EVAL_HISTORY_TURNS = 4         # 评审参考的最近对话轮数(消解多轮指代, 限量控 token)
_CATEGORY_LABELS = {"image": "图片", "memory": "记忆"}  # kb_context 来源标签


def _image_to_model_url(image: str) -> Optional[str]:
    """把图片引用转成模型可用 url: URL 直通, base64 data URI 直通, 本地路径转 base64。"""
    if not image:
        return None
    low = image.strip().lower()
    if low.startswith(("http://", "https://", "data:image/")):
        return image.strip()
    # 本地文件路径 → base64 data URI(复用 llm_init.image_to_base64)
    api_img, _ = image_to_base64(image)
    return api_img or None


def _extract_evidence(answer: str, kb_context: list) -> list:
    """从回答扫描 (检索内容N) 引用, 收集被引用的 doc 作为前端证据(图片 + 文本来源)。

    严格模式: 扫描不到任何引用返回 []。同一 doc 被多个 chunk 引用时去重
    (图片按 image_path, 文本按 filename), 保留全部引用编号 indexes。
    Returns:
        [{"type": "image"|"text", "filename", "indexes": [...], "image_path"|"text"}, ...]
    """
    if not answer or not kb_context:
        return []
    by_key: dict = {}
    for m in _CITE_RE.finditer(answer):
        idx = int(m.group(1)) - 1
        if not (0 <= idx < len(kb_context)):
            continue
        hit = kb_context[idx]
        filename = hit.get("filename") or ""
        if hit.get("category") == "image" and hit.get("image_path"):
            key = ("image", hit["image_path"])
            item = by_key.get(key)
            if item is None:
                item = {"type": "image", "filename": filename,
                        "image_path": hit["image_path"], "indexes": []}
                by_key[key] = item
        else:
            # 文本来源卡片(文档或历史记忆): 记忆无文件名, 按内容去重(每条被引用的
            # 记忆各自一张卡), 文本按文件名去重; label 区分"历史记忆/文档"供前端展示
            snippet = (hit.get("text") or "")[:120]
            is_memory = hit.get("category") == "memory"
            key = ("memory", snippet) if is_memory else ("text", filename)
            item = by_key.get(key)
            if item is None:
                item = {"type": "text", "filename": filename,
                        "label": "历史记忆" if is_memory else (filename or "文档"),
                        "text": snippet, "indexes": []}
                by_key[key] = item
        num = idx + 1
        if num not in item["indexes"]:
            item["indexes"].append(num)
    return list(by_key.values())


def _retrieved_images_for_model(kb_images: list, limit: int = RETRIEVED_IMAGES_TO_MODEL) -> list:
    """取检索命中的前 limit 张图(按 RRF 名次), 仅供 evaluate 核实时参考。

    生成器方案B 不把检索图进模型(以 [图片] 标签 doc 文本作答); 但回答会引用
    知识库图片, judge 若看不到就无法核实引用, 会把忠实描述误判为幻觉
    (faithfulness=2.0 案例), 故评审单独参考检索图。
    """
    return [img for img in (kb_images or []) if img][:limit]


def _history_images_from_messages(messages: list, limit: int = HISTORY_IMAGES_TO_MODEL) -> list:
    """取窗口内 HumanMessage 携带的图(最近优先), 供 judge 还原生成器看到的历史图。"""
    working = get_working_window(messages)
    images = []
    for msg in reversed(working):
        if len(images) >= limit:
            break
        img = _message_image_url(msg)
        if img:
            images.append(img)
    return images
