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
async def job_status(job_id: str):
    """查询入库任务状态。"""
    job = await asyncio.to_thread(get_job, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在",
        )
    return job


@router.get("/jobs")
async def jobs_list(limit: int = 20):
    """最近入库任务列表(按创建时间倒序)。"""
    jobs = await asyncio.to_thread(list_jobs, max(1, min(limit, 100)))
    return {"jobs": jobs}


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
