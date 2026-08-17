"""知识库路由(仅管理员): 上传 PDF → 异步入库任务; 查询任务状态; 知识库真实统计。

入库管道(OCR→分片→描述→向量化→Milvus)由 ingestion 包在后台线程执行,
上传接口立即返回 job_id, 前端轮询 GET /knowledge/jobs/{id} 获取进度。
"""
import asyncio
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from loguru import logger

from core.config import settings
from core.deps import require_admin
from ingestion.documents import (
    cleanup_job_files,
    count_documents_by_filename,
    count_documents_by_md5,
    get_document,
    list_uploads,
    remove_document,
)
from ingestion.embed import (
    delete_milvus_by_ids,
    query_milvus_chunks_by_filename,
    query_milvus_chunks_by_ids,
    query_milvus_ids_by_filename,
)
from ingestion.jobs import (
    cancel_job,
    count_jobs_by_status,
    get_job,
    list_jobs,
    pause_job,
    remove_job,
    resume_job,
    retry_job,
    start_index,
    start_job,
    start_parse,
)
from models.user import User
from services.image_store import resolve_image_url

router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    mode: str = Form("auto"),
    current_user: User = Depends(require_admin),
):
    """上传 PDF 到知识库, 触发异步入库管道, 立即返回 job_id。

    mode: auto 自动全流程(默认) / manual 上传后先不处理(停在首阶段, 点「继续」后一路处理)。

    A12 修复: 流式分块落盘 + 大小上限(MAX_PDF_UPLOAD_MB, 超限 413), 不再整文件读入内存;
    D2 修复: 落盘同时算源文件 md5, 与已入库文档按内容查重(重复 409, 避免重复向量)。
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 PDF 文件",
        )
    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode 仅支持 auto/manual")

    max_bytes = settings.MAX_PDF_UPLOAD_MB * 1024 * 1024
    os.makedirs(settings.INGEST_TMP_DIR, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    pdf_path = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")

    # 流式读取: 边读边写边算 md5; 超限立即中止并清理, 不占内存
    md5 = hashlib.md5()
    total = 0
    with open(pdf_path, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)  # 1MB 分块
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                f.close()
                os.remove(pdf_path)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"PDF 超过大小限制({settings.MAX_PDF_UPLOAD_MB}MB)",
                )
            md5.update(chunk)
            f.write(chunk)
    if total == 0:
        os.remove(pdf_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")

    file_md5 = md5.hexdigest()
    # D2: 内容查重 —— 同 md5 已入库则拒绝(409), 避免重复文档行+重复 Milvus 向量
    if await asyncio.to_thread(count_documents_by_md5, file_md5) > 0:
        os.remove(pdf_path)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该文件已入库过(内容相同), 请勿重复上传",
        )

    size_mb = total / (1024 * 1024)
    logger.info("管理员 {} 上传 PDF: {}, 大小 {:.2f} MB, 模式={}", current_user.username, file.filename, size_mb, mode)

    original_name = os.path.basename(file.filename)
    # start_job 写 MySQL(同步引擎), 放线程避免阻塞事件循环
    # 关键: 把 pdf_path 的 job_id 一并传给 create_job, 保证 DB 任务 id == 磁盘 PDF 文件名
    started = await asyncio.to_thread(start_job, pdf_path, original_name, current_user.id, mode, job_id)
    logger.info("[入库] 任务已创建: job={}, filename={}, mode={}", started, original_name, mode)

    return {"job_id": started, "status": "pending", "filename": original_name, "run_mode": mode}


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
    status: str = "",
    current_user: User = Depends(require_admin),
):
    """最近入库任务列表(按创建时间倒序, 可按 status 筛选, 仅管理员)。"""
    jobs = await asyncio.to_thread(
        list_jobs, max(1, min(limit, 100)), status or None
    )
    return {"jobs": jobs}


@router.delete("/jobs/{job_id}")
async def jobs_delete(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """删除失败/已取消的任务记录并清理其中间产物(仅管理员)。"""
    try:
        ok = await asyncio.to_thread(remove_job, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {"deleted": True}


@router.post("/jobs/{job_id}/retry")
async def jobs_retry(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """重试失败任务: 复用原 job_id, 重置状态后重新入队(仅管理员)。"""
    try:
        await asyncio.to_thread(retry_job, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    job = await asyncio.to_thread(get_job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {"job_id": job_id, "status": "pending", "filename": job["filename"]}


@router.post("/jobs/{job_id}/cancel")
async def jobs_cancel(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """取消 running/pending 任务(协作式: 当前阶段结束后的检查点生效, 仅管理员)。"""
    ok = await asyncio.to_thread(cancel_job, job_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务不存在或当前状态不可取消(仅 pending/running 可取消)",
        )
    return {"job_id": job_id, "cancelling": True}


@router.post("/jobs/{job_id}/parse")
async def jobs_parse(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """手动控制: 触发解析段(OCR/分片)。仅「待解析」状态可触发(仅管理员)。"""
    try:
        ok = await asyncio.to_thread(start_parse, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {"job_id": job_id, "started": True}


@router.post("/jobs/{job_id}/index")
async def jobs_index(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """手动控制: 触发索引段(描述/向量化/入库)。仅「解析完成待入库」状态可触发(仅管理员)。"""
    try:
        ok = await asyncio.to_thread(start_index, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {"job_id": job_id, "started": True}


@router.post("/jobs/{job_id}/pause")
async def jobs_pause(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """暂停任务: 当前阶段结束后在下一阶段边界停下(仅管理员)。"""
    ok = await asyncio.to_thread(pause_job, job_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务不存在或当前状态不可暂停",
        )
    return {"job_id": job_id, "pausing": True}


@router.post("/jobs/{job_id}/resume")
async def jobs_resume(
    job_id: str,
    current_user: User = Depends(require_admin),
):
    """继续任务: 放行暂停中的任务, 之后一路跑完剩余阶段(仅管理员)。"""
    ok = await asyncio.to_thread(resume_job, job_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务当前未处于暂停状态",
        )
    return {"job_id": job_id, "resumed": True}


@router.get("/documents")
async def documents_list(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    status: str = "",
    filetype: str = "",
    current_user: User = Depends(require_admin),
):
    """文件分页列表(仅管理员): 已入库文档 + 进行中/失败/已取消的上传任务。

    keyword 文件名模糊过滤; status 逗号分隔(同时匹配任务/文档状态); filetype 仅文档生效。
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    items, total = await asyncio.to_thread(
        list_uploads, page, page_size, keyword, status or ""
    )
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
        from infra.config import COLLECTION_NAME
        from infra.milvus import milvus_client
        from ingestion.documents import count_documents, sum_char_count

        stats = milvus_client.get_collection_stats(collection_name=COLLECTION_NAME)
        vector_count = int(stats.get("row_count", 0))
        doc_count = await asyncio.to_thread(count_documents)
        total_chars = await asyncio.to_thread(sum_char_count)
        failed_jobs = await asyncio.to_thread(count_jobs_by_status, "error")
        return {
            "status": "ok",
            "collections": [{"name": COLLECTION_NAME, "document_count": vector_count}],
            "total_documents": doc_count,       # MySQL knowledge_documents 文档数
            "total_vectors": vector_count,      # Milvus chunk 数
            "total_chars": total_chars,         # 全部文档字符数合计
            "failed_jobs": failed_jobs,         # 失败入库任务数
        }
    except Exception as e:
        logger.warning("查询知识库状态失败(降级): {}", e)
        return {
            "status": "degraded",
            "total_documents": 0,
            "total_vectors": 0,
            "total_chars": 0,
            "failed_jobs": 0,
            "message": f"Milvus 不可用: {e}",
        }
