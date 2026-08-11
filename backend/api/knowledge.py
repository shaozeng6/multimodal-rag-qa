"""知识库路由(仅管理员): 上传 PDF → 异步入库任务; 查询任务状态; 知识库真实统计。

入库管道(OCR→分片→描述→向量化→Milvus)由 ingestion 包在后台线程执行,
上传接口立即返回 job_id, 前端轮询 GET /knowledge/jobs/{id} 获取进度。
"""
import asyncio
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from loguru import logger

from core.config import settings
from models.user import User
from core.deps import require_admin
from ingestion.jobs import start_job, get_job, list_jobs
from ingestion.documents import (
    list_documents,
    get_document,
    remove_document,
    count_documents_by_filename,
    cleanup_job_files,
)
from ingestion.embed import (
    delete_milvus_by_ids,
    query_milvus_ids_by_filename,
    query_milvus_chunks_by_ids,
    query_milvus_chunks_by_filename,
)
from services.image_store import resolve_image_url

router = APIRouter(prefix="/knowledge", tags=["知识库"])


def _write_file(path: str, content: bytes) -> None:
    """同步写文件(放到 to_thread 中执行, 避免阻塞事件循环)。"""
    with open(path, "wb") as f:
        f.write(content)


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
):
    """上传 PDF 到知识库, 触发异步入库管道, 立即返回 job_id。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 PDF 文件",
        )

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    logger.info("管理员 {} 上传 PDF: {}, 大小 {:.2f} MB", current_user.username, file.filename, size_mb)

    # 保存到临时目录(文件名用 job_id, 避免冲突; 真实文件名仅作元数据)
    os.makedirs(settings.INGEST_TMP_DIR, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    pdf_path = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")
    await asyncio.to_thread(_write_file, pdf_path, content)

    original_name = os.path.basename(file.filename)
    # start_job 写 MySQL(同步引擎), 放线程避免阻塞事件循环
    started = await asyncio.to_thread(start_job, pdf_path, original_name, current_user.id)
    logger.info("[入库] 任务已创建: job={}, filename={}", started, original_name)

    return {"job_id": started, "status": "pending", "filename": original_name}


@router.get("/jobs/{job_id}")
async def job_status(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """查询入库任务状态(仅管理员; 修 A4: 原无鉴权可枚举任意 job)。"""
    job = await asyncio.to_thread(get_job, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在",
        )
    return job


@router.get("/jobs")
async def jobs_list(
    limit: int = 20,
    current_user: User = Depends(require_admin),
):
    """最近入库任务列表(按创建时间倒序, 仅管理员)。"""
    jobs = await asyncio.to_thread(list_jobs, max(1, min(limit, 100)))
    return {"jobs": jobs}


@router.get("/documents")
async def documents_list(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    current_user: User = Depends(require_admin),
):
    """知识文档分页列表(仅管理员), keyword 按文件名模糊过滤。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    items, total = await asyncio.to_thread(list_documents, page, page_size, keyword)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.delete("/documents/{doc_id}")
async def documents_delete(
    doc_id: int,
    current_user: User = Depends(require_admin),
):
    """删除单个文档: 先删 Milvus 向量, 再清理磁盘产物, 最后删 MySQL 行(仅管理员)。

    顺序保证一致性: Milvus 删除失败则返回 500 且不删 MySQL 行,
    避免"向量残留但元数据消失"的悬垂态(检索仍会命中已删文档)。
    删除方式: 优先按持久化 milvus_ids 精确删; 存量文档(ids 为空)按 filename 兜底,
    但仅当该 filename 唯一才允许, 同名多文档返回 409 防误删。
    """
    doc = await asyncio.to_thread(get_document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 1) 删 Milvus 向量
    milvus_ids = doc.get("milvus_ids")
    milvus_deleted = 0
    try:
        if milvus_ids:
            milvus_deleted = await asyncio.to_thread(delete_milvus_by_ids, list(milvus_ids))
        else:
            # legacy 兜底: 存量文档无 ids, 仅当该 filename 唯一才允许按名删
            if await asyncio.to_thread(count_documents_by_filename, doc["filename"]) > 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该文档为历史入库(无向量索引记录), 且存在同名文档; 为避免误删请先重新入库",
                )
            legacy_ids = await asyncio.to_thread(query_milvus_ids_by_filename, doc["filename"])
            if legacy_ids:
                milvus_deleted = await asyncio.to_thread(delete_milvus_by_ids, legacy_ids)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("删除文档 {} 时 Milvus 操作失败: {}", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除向量失败, 文档未删除, 请稍后重试",
        )

    # 2) 清理磁盘产物(OCR md 目录 + 临时 PDF; 图片目录共享不删)
    await asyncio.to_thread(cleanup_job_files, doc.get("job_id") or "")

    # 3) 删 MySQL 行
    await asyncio.to_thread(remove_document, doc_id)

    return {"deleted": True, "milvus_deleted": milvus_deleted}


@router.get("/documents/{doc_id}/chunks")
async def document_chunks(
    doc_id: int,
    current_user: User = Depends(require_admin),
):
    """查看某文档的 chunk 明细(文本/图片, 按入库顺序), 供管理页点开文档下钻。

    - 优先按持久化 milvus_ids 精确查(保序);
    - 存量文档(ids 为空)按 filename 兜底(展示用, 同名多文档会并集)。
    - 图片 chunk 的 image_path 统一 resolve 成前端可加载 URL。
    """
    doc = await asyncio.to_thread(get_document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )
    milvus_ids = doc.get("milvus_ids")
    if milvus_ids:
        chunks = await asyncio.to_thread(query_milvus_chunks_by_ids, list(milvus_ids))
    else:
        chunks = await asyncio.to_thread(query_milvus_chunks_by_filename, doc["filename"])
    for c in chunks:
        if c.get("image_path"):
            url = resolve_image_url(c["image_path"])
            if url:
                c["url"] = url
    return {"items": chunks, "total": len(chunks)}


@router.get("/status")
async def knowledge_status(current_user: User = Depends(require_admin)):
    """知识库真实统计: Milvus t_doc_collection 实体数 + MySQL 文档元数据。"""
    try:
        from graph.llm_init import milvus_client, COLLECTION_NAME
        from ingestion.documents import count_documents

        stats = milvus_client.get_collection_stats(collection_name=COLLECTION_NAME)
        vector_count = int(stats.get("row_count", 0))
        doc_count = await asyncio.to_thread(count_documents)
        return {
            "status": "ok",
            "collections": [{"name": COLLECTION_NAME, "document_count": vector_count}],
            "total_documents": doc_count,       # MySQL knowledge_documents 文档数
            "total_vectors": vector_count,      # Milvus chunk 数
        }
    except Exception as e:
        logger.warning("查询知识库状态失败(降级): {}", e)
        return {
            "status": "degraded",
            "total_documents": 0,
            "total_vectors": 0,
            "message": f"Milvus 不可用: {e}",
        }
