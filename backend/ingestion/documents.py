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
from models.ingestion import KnowledgeDocument
from models.user import User
from ingestion.sync_db import SyncSession


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
) -> None:
    """记录一个入库成功的文档(失败不入库, 失败详情看 ingest_jobs.error)。

    milvus_ids: write_to_milvus 返回的 Milvus 主键, 供文档级精确删除。
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
        ))
        db.commit()
    logger.info("[入库] 文档元数据已记录: filename={}, chunks={}, images={}",
                filename, chunk_count, image_count)


def count_documents() -> int:
    """知识文档总数(供 /knowledge/status 统计)。"""
    with SyncSession() as db:
        return db.execute(
            select(func.count()).select_from(KnowledgeDocument)
        ).scalar_one() or 0


def list_documents(page: int = 1, page_size: int = 20, keyword: str = "") -> Tuple[List[dict], int]:
    """知识文档分页列表(按创建时间倒序), keyword 按 filename 模糊过滤。

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
        total = db.execute(count_q).scalar_one() or 0
        rows = db.execute(
            q.offset((page - 1) * page_size).limit(page_size)
        ).all()
        items = [_doc_to_dict(doc, uploader_name) for doc, uploader_name in rows]
        return items, total


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
