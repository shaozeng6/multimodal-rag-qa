"""LLM 输出 JSON 健壮解析(公共工具, P0-4)。

背景: 图片理解(image_analysis)与评审(nodes_evaluate)原先各自用
`re.search(r'\\{[^}]+\\}', raw)` 从模型输出抠 JSON, 同一脆弱模式导致:
- 多模态模型返回块式结构([{'type':'text','text': ...}])时, str() 后正则从外层
  dict 的 `{` 截到首个 `}` → 永远解析失败(C3)
- caption/回答内容含花括号(代码片段)时被截断(KNOWN_ISSUES #1)
- markdown 围栏(```json)未剥离

本模块统一处理: 块列表取 text 块 → 剥围栏 → 逐个"平衡花括号"候选
(字符串字面量内的花括号不计深度) → json.loads, 任一候选成功即返回。
"""
import json
import re
from typing import Any, Dict, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _content_to_text(raw: Any) -> str:
    """把 LLM 输出归一化为纯文本:
    - str: 原样
    - OpenAI 兼容块列表: 取 text 块内容(忽略 image_url 等), 避免 str(列表) 后
      正则命中外层 dict 的花括号
    - 其它(嵌套 dict 等): str() 兜底
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        if parts:
            return "".join(parts)
    return str(raw)


def _balanced_from(text: str, start: int) -> Optional[str]:
    """从 start 位置的 '{' 开始做平衡花括号匹配, 返回闭合完整的 JSON 对象字面量。

    字符串字面量内的花括号(如 caption 里的代码片段)不计入深度, 因此
    '{"caption": "图中是 {key: value}"}' 能完整取到。
    """
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(raw: Any) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中稳健提取 JSON 对象; 提取/解析失败返回 None。

    从每个 '{' 位置依次尝试平衡匹配 + json.loads, 第一个成功即返回:
    正文里恰好出现成对花括号(如 "图中有 {a:1}" 之类)时不会被误取。
    """
    if raw is None:
        return None
    text = _content_to_text(raw)
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start = 0
    while True:
        start = text.find("{", start)
        if start < 0:
            return None
        candidate = _balanced_from(text, start)
        if candidate is not None:
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                start += 1
                continue
            return data if isinstance(data, dict) else None
        start += 1
    return None
