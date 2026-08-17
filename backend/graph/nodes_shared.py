"""graph 节点共享工具: 图片进模型辅助 + 引用证据提取 + 常量。

方案B 后生成器不再把检索图注入模型视野(知识库图片以 [图片] 标签 doc 文本进上下文,
答案按 检索内容N 引用, 对应图片由前端证据区展示); 仅当前输入图进用户消息,
历史图随 messages 直传。评审节点需与生成器看到的口径一致, 故图集与证据逻辑
抽到本模块供 generator/evaluate/regenerate 复用。
"""
import re
from typing import Optional

from graph.context import _message_image_url, get_working_window
from infra.dashscope import image_to_base64
from services.config_service import get_int

# ========= 图片进模型: 上限控制, 控 token(运行时由 sys_config 覆盖, 常量作默认值兜底) =========
RETRIEVED_IMAGES_TO_MODEL = 2  # 检索命中的图, 评审最多参考几张(仅 evaluate 核实用)
HISTORY_IMAGES_TO_MODEL = 1    # 历史对话里的图, 最多还原几张(judge 用)
MAX_IMAGES_TO_MODEL = 4        # 当前输入图 + 历史图 总上限

# ========= 引用证据(方案B): 从回答扫描引用标记, 定位被引用的来源 doc =========
# 兼容模型常见的格式漂移:
# - "检索内容N"/"检索文档N"/"检索资料N"/"检索来源N" + 半角/全角括号(括号可在词前/词后/词与数字间)
# - 裸数字引用: "[N]" / "[[N]" / "【N】" / "（N）" (模型偶发不带"检索内容"前缀)
_CITE_RE = re.compile(r"[【\[（(]{0,2}\s*(?:检索(?:内容|文档|资料|来源))?\s*[【\[（(]?\s*(\d+)\s*[】\]）)]+")

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
    # 本地文件路径 → base64 data URI(复用 infra.dashscope.image_to_base64)
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
            # 记忆各自一张卡), 文本按文件名去重; label 区分"历史记忆/文档"供前端展示。
            # 存全文供前端"点击展开"查看完整来源(前端展示时自行截断预览)。
            full_text = hit.get("text") or ""
            is_memory = hit.get("category") == "memory"
            key = ("memory", full_text) if is_memory else ("text", filename)
            item = by_key.get(key)
            if item is None:
                item = {"type": "text", "filename": filename,
                        "label": "历史记忆" if is_memory else (filename or "文档"),
                        "text": full_text, "indexes": []}
                by_key[key] = item
        num = idx + 1
        if num not in item["indexes"]:
            item["indexes"].append(num)
    return list(by_key.values())


def _lexical_overlap(text_a: str, text_b: str) -> float:
    """中文 bigram 重合率: text_b 中多少比例的 bigram 出现在 text_a 中(零 API 成本词法相似度)。

    用于模型未引用时的兜底归因; 数值越高说明 text_b 的内容越被 text_a 用到。
    """
    if not text_a or not text_b:
        return 0.0
    grams_a = {text_a[i : i + 2] for i in range(len(text_a) - 1)}
    grams_b = {text_b[i : i + 2] for i in range(len(text_b) - 1)}
    if not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_b)


def _auto_attribution_evidence(answer: str, kb_context: list, max_sources: int = 2) -> list:
    """无引用兜底: 按词法相似度为回答匹配最相关来源, 生成证据项(零 API 成本)。

    模型可能漏标/不标引用; 这里把回答与各检索来源做 bigram 重合度匹配,
    取最相关的 1-2 条作为证据卡。只设置后端 evidence 供前端展示来源(不注入正文标记,
    因流式输出已完成, 避免 live/历史不一致)。阈值可据实测调优。
    """
    if not answer or not kb_context:
        return []
    scored = []
    for i, hit in enumerate(kb_context, 1):
        text = hit.get("text") or ""
        if not text:
            continue
        score = _lexical_overlap(answer, text)
        if score >= 0.06:  # 至少 6% 的来源内容在回答里体现, 才视为相关
            scored.append((score, i, hit))
    scored.sort(key=lambda x: x[0], reverse=True)

    evidence: list = []
    for _, idx, hit in scored[:max_sources]:
        filename = hit.get("filename") or ""
        if hit.get("category") == "image" and hit.get("image_path"):
            evidence.append({
                "type": "image", "filename": filename,
                "image_path": hit["image_path"], "indexes": [idx],
            })
        else:
            full_text = hit.get("text") or ""
            is_memory = hit.get("category") == "memory"
            evidence.append({
                "type": "text", "filename": filename,
                "label": "历史记忆" if is_memory else (filename or "文档"),
                "text": full_text, "indexes": [idx],
            })
    return evidence


def _retrieved_images_for_model(kb_images: list, limit: Optional[int] = None) -> list:
    """取检索命中的前 limit 张图(按 RRF 名次), 仅供 evaluate 核实时参考。

    生成器方案B 不把检索图进模型(以 [图片] 标签 doc 文本作答); 但回答会引用
    知识库图片, judge 若看不到就无法核实引用, 会把忠实描述误判为幻觉
    (faithfulness=2.0 案例), 故评审单独参考检索图。
    """
    if limit is None:
        limit = get_int("rag.retrieved_images_to_model", RETRIEVED_IMAGES_TO_MODEL)
    return [img for img in (kb_images or []) if img][:limit]


def _history_images_from_messages(messages: list, limit: Optional[int] = None) -> list:
    """取窗口内 HumanMessage 携带的图(最近优先), 供 judge 还原生成器看到的历史图。"""
    if limit is None:
        limit = get_int("rag.history_images_to_model", HISTORY_IMAGES_TO_MODEL)
    working = get_working_window(messages)
    images = []
    for msg in reversed(working):
        if len(images) >= limit:
            break
        img = _message_image_url(msg)
        if img:
            images.append(img)
    return images
