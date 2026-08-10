"""消息图片存储: base64 data URI → 本地文件, 存引用不存 base64(schema_v2)。

目录由 settings.UPLOAD_IMAGES_DIR 指定, 经 main.py 的 /uploads 静态挂载暴露。
返回的是可被前端直接加载的 URL 路径(/uploads/xxx.png)。
"""
import base64
import binascii
import os
import re
import urllib.parse
import uuid

from loguru import logger

from core.config import settings

_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)
_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_SAFE_BYTES = 10 * 1024 * 1024  # 单图上限 10MB, 防超大 base64


def save_image_from_data_uri(data_uri: str) -> str | None:
    """把 base64 data URI 保存为文件, 返回 URL 路径(/uploads/xx.ext)。

    解析失败 / 超限 / 写入失败返回 None(调用方按无图处理, 不阻断流程)。
    """
    if not data_uri:
        return None
    match = _DATA_URI_RE.match(data_uri)
    if not match:
        # 已是 URL 或本地路径, 直接透传(非 base64 输入)
        return data_uri if data_uri.strip().lower().startswith(("http://", "https://", "/")) else None

    mime = match.group("mime").lower()
    ext = _MIME_EXT.get(mime, ".png")
    try:
        raw = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError) as e:
        logger.warning("base64 图片解码失败: {}", e)
        return None
    if not raw or len(raw) > _SAFE_BYTES:
        logger.warning("图片为空或超过 10MB 限制, 拒绝保存")
        return None

    os.makedirs(settings.UPLOAD_IMAGES_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(settings.UPLOAD_IMAGES_DIR, filename)
    try:
        with open(filepath, "wb") as f:
            f.write(raw)
    except OSError as e:
        logger.exception("图片写入失败: {}", e)
        return None
    return f"/uploads/{filename}"


def resolve_image_url(image_ref: str) -> str | None:
    """把入库的图片引用解析为前端可加载的 URL。

    - http(s):// / data:image/ / 以 / 开头 → 原样返回(已是 URL)
    - 其余视为本地文件路径 → 返回 /api/files?path=...(由后端端点做路径安全校验后服务)
    """
    if not image_ref:
        return None
    s = image_ref.strip()
    low = s.lower()
    if low.startswith(("http://", "https://", "data:image/")) or s.startswith("/"):
        return s
    return f"/api/files?path={urllib.parse.quote(s)}"
