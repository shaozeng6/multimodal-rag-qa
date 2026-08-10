"""文件服务路由: 把入库的本地图片路径转成前端可加载的 URL(/api/files?path=...)。

背景: Milvus t_doc 的 image_path 是本地文件路径(旧数据为绝对路径, 新入库为相对路径),
浏览器无法直接加载, 由本端点做路径安全校验后服务。

安全: 只允许提供【允许根目录】下的图片文件, 防止任意文件读取(路径穿越)。
允许根目录 = INGEST_OUTPUT_DIR / INGEST_IMAGES_DIR / KB_IMAGE_ROOTS(配置)。
"""
import mimetypes
import os

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from core.config import settings

router = APIRouter(tags=["文件"])

# 允许服务的图片扩展名
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _allowed_roots() -> list:
    """允许提供文件的根目录(realpath 列表)。"""
    roots = [settings.INGEST_OUTPUT_DIR, settings.INGEST_IMAGES_DIR]
    for extra in (settings.KB_IMAGE_ROOTS or "").split(";"):
        if extra.strip():
            roots.append(extra.strip())
    return [os.path.realpath(r) for r in roots if r]


def _safe_resolve(path: str) -> str | None:
    """把请求路径解析为可服务的文件绝对路径, 非法返回 None。

    - 路径穿越检查: realpath 必须落在允许根目录内(Windows 大小写不敏感)
    - 必须是存在的图片文件
    """
    if not path:
        return None
    try:
        real = os.path.realpath(path)
    except OSError:
        return None

    allowed = False
    try:
        for root in _allowed_roots():
            common = os.path.commonpath([os.path.normcase(root), os.path.normcase(real)])
            if common == os.path.normcase(root):
                allowed = True
                break
    except ValueError:
        return None
    if not allowed:
        return None

    if not os.path.isfile(real):
        return None
    if not os.path.splitext(real)[1].lower() in _IMAGE_EXTS:
        return None
    return real


@router.get("/files")
async def serve_image(path: str = ""):
    """按入库 image_path 提供图片文件。"""
    filepath = _safe_resolve(path)
    if filepath is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在或不在允许目录内",
        )
    media_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return FileResponse(filepath, media_type=media_type)
