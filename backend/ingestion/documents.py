"""知识文档元数据记录(knowledge_documents 表, schema_v2)。

与 Milvus t_doc_collection 的 chunk 对应: 一个源文件一条记录(文档级),
chunk 实体在向量库。入库管道成功后由 pipeline 调用。
文档管理(列表/删除)供 /knowledge API 使用。
"""
import os
import shutil
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, select

from core.config import settings
from ingestion.sync_db import SyncSession
from models.ingestion import IngestJob, KnowledgeDocument
from models.user import User


def _doc_to_dict(doc: KnowledgeDocument, uploader_name: Optional[str] = None) -> dict:
    """KnowledgeDocument ORM → 可 JSON 序列化 dict。

    milvus_ids 保留给删除端点精确删向量用(仅为 Milvus 主键, 非敏感信息)。
    """
    return {
        "id": doc.id,
        "job_id": doc.job_id,
        "filename": doc.filename,
        "filetype": doc.filetype,
        "title": doc.title or "",
        "status": doc.status,
        "chunk_count": doc.chunk_count or 0,
        "image_count": doc.image_count or 0,
        "char_count": doc.char_count or 0,
        "file_size": doc.file_size or 0,
        "file_md5": doc.file_md5,
        "uploaded_by": doc.uploaded_by,
        "uploader_name": uploader_name,
        "milvus_ids": doc.milvus_ids,
        "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M:%S") if doc.created_at else None,
        "updated_at": doc.updated_at.strftime("%Y-%m-%d %H:%M:%S") if doc.updated_at else None,
    }


def record_document(
    job_id: str,
    filename: str,
    user_id: Optional[int],
    file_md5: Optional[str],
    chunk_count: int,
    image_count: int,
    status: str = "ingested",
    milvus_ids: Optional[List[int]] = None,
    char_count: int = 0,
    file_size: int = 0,
) -> None:
    """记录一个入库成功的文档(失败不入库, 失败详情看 ingest_jobs.error)。

    milvus_ids: write_to_milvus 返回的 Milvus 主键, 供文档级精确删除。
    char_count: 文本类 chunk 字符数合计(含图片/表格描述), 供管理页展示。
    file_size: 源文件字节数(上传接口记录)。
    """
    with SyncSession() as db:
        db.add(KnowledgeDocument(
            job_id=job_id,
            filename=filename,
            filetype="pdf",
            status=status,
            chunk_count=chunk_count,
            image_count=image_count,
            file_md5=file_md5,
            uploaded_by=user_id,
            milvus_ids=milvus_ids,
            char_count=char_count,
            file_size=file_size,
        ))
        db.commit()
    logger.info("[入库] 文档元数据已记录: filename={}, chunks={}, images={}, chars={}",
                filename, chunk_count, image_count, char_count)


def count_documents() -> int:
    """知识文档总数(供 /knowledge/status 统计)。"""
    with SyncSession() as db:
        return db.execute(
            select(func.count()).select_from(KnowledgeDocument)
        ).scalar_one() or 0


def list_documents(page: int = 1, page_size: int = 20, keyword: str = "",
                   status: str = "", filetype: str = "") -> Tuple[List[dict], int]:
    """知识文档分页列表(按创建时间倒序), keyword 按 filename 模糊过滤, status/filetype 精确筛选。

    Returns:
        (items, total): items 为 _doc_to_dict 序列化结果, 含 uploader_name(join users)。
    """
    with SyncSession() as db:
        count_q = select(func.count()).select_from(KnowledgeDocument)
        q = (
            select(KnowledgeDocument, User.username)
            .outerjoin(User, KnowledgeDocument.uploaded_by == User.id)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        )
        if keyword:
            like = f"%{keyword}%"
            count_q = count_q.where(KnowledgeDocument.filename.like(like))
            q = q.where(KnowledgeDocument.filename.like(like))
        if status:
            count_q = count_q.where(KnowledgeDocument.status == status)
            q = q.where(KnowledgeDocument.status == status)
        if filetype:
            count_q = count_q.where(KnowledgeDocument.filetype == filetype)
            q = q.where(KnowledgeDocument.filetype == filetype)
        total = db.execute(count_q).scalar_one() or 0
        rows = db.execute(
            q.offset((page - 1) * page_size).limit(page_size)
        ).all()
        items = [_doc_to_dict(doc, uploader_name) for doc, uploader_name in rows]
        return items, total


def list_uploads(page: int = 1, page_size: int = 20, keyword: str = "",
                 status: str = "") -> Tuple[List[dict], int]:
    """合并「进行中/失败/已取消的入库任务」+「已入库文档」为一个统一列表(按时间倒序)。

    供知识库管理页文件列表展示: 上传中的文件也出现在列表里, 带阶段状态与进度, 无需单独的"入库任务"页。
    - 任务侧: ingest_jobs 中非 success 状态(尚未生成 knowledge_documents 行)
    - 文档侧: knowledge_documents(与 success 任务 1:1)
    - 任务行带 kind=job + job 字段(progress/stage/stage_detail/run_mode/paused/error/log)
    - status 参数: 逗号分隔, 同时匹配 job.status 与 doc.status
    """
    from ingestion.jobs import _job_to_dict  # 复用任务序列化(含 paused/log)

    with SyncSession() as db:
        job_rows = db.execute(
            select(IngestJob).where(
                IngestJob.status.in_(("pending", "running", "error", "cancelled"))
            )
        ).scalars().all()
        doc_rows = db.execute(
            select(KnowledgeDocument, User.username)
            .outerjoin(User, KnowledgeDocument.uploaded_by == User.id)
        ).all()

    entries: list = []
    for j in job_rows:
        d = _job_to_dict(j)
        d["kind"] = "job"
        d["chunk_count"] = None
        d["image_count"] = None
        d["char_count"] = None
        d["file_size"] = None
        d["uploader_name"] = None
        entries.append(d)
    for doc, uploader_name in doc_rows:
        d = _doc_to_dict(doc, uploader_name)
        d["kind"] = "doc"
        entries.append(d)

    if keyword:
        kw = keyword.lower()
        entries = [e for e in entries if kw in (e.get("filename") or "").lower()]
    if status:
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        if status_list:
            entries = [e for e in entries if (e.get("status") or "") in status_list]

    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    total = len(entries)
    start = (page - 1) * page_size
    return entries[start:start + page_size], total


def sum_char_count() -> int:
    """全部文档字符数合计(供知识库统计)。"""
    with SyncSession() as db:
        return db.execute(
            select(func.coalesce(func.sum(KnowledgeDocument.char_count), 0))
        ).scalar_one() or 0


def get_document(doc_id: int) -> Optional[dict]:
    """按主键取单个文档(供删除前校验), 不存在返回 None。"""
    with SyncSession() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        if doc is None:
            return None
        return _doc_to_dict(doc)


def remove_document(doc_id: int) -> None:
    """硬删 knowledge_documents 行(Milvus 向量与磁盘产物由调用方先清理)。"""
    with SyncSession() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        if doc is not None:
            db.delete(doc)
            db.commit()
            logger.info("[知识库] 文档元数据已删除: id={}", doc_id)


def count_documents_by_filename(filename: str) -> int:
    """同名文档数量(legacy 兜底删除时判断唯一性, 避免误删同名)。"""
    with SyncSession() as db:
        return db.execute(
            select(func.count()).select_from(KnowledgeDocument)
            .where(KnowledgeDocument.filename == filename)
        ).scalar_one() or 0


def count_documents_by_md5(file_md5: str) -> int:
    """按源文件 md5 统计已入库文档数(上传去重, 修 D2)。

    只统计已入库文档(knowledge_documents), 失败/进行中任务不计;
    配合上传接口在入库前查重, 避免同文件重复上传产生重复文档行+重复向量。
    """
    if not file_md5:
        return 0
    with SyncSession() as db:
        return db.execute(
            select(func.count()).select_from(KnowledgeDocument)
            .where(KnowledgeDocument.file_md5 == file_md5)
        ).scalar_one() or 0


def cleanup_job_files(job_id: str) -> None:
    """清理该入库任务的磁盘产物: OCR md 目录 + 临时 PDF。

    图片目录(INGEST_IMAGES_DIR)为 md5 内容哈希命名、多文档可能共享, 不删。
    """
    if not job_id:
        return
    out_dir = os.path.join(settings.INGEST_OUTPUT_DIR, job_id)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
        logger.info("[知识库] 已清理 OCR 目录: {}", out_dir)
    tmp_pdf = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")
    if os.path.isfile(tmp_pdf):
        os.remove(tmp_pdf)
        logger.info("[知识库] 已清理临时 PDF: {}", tmp_pdf)
