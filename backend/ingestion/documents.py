"""知识文档元数据记录(knowledge_documents 表, schema_v2)。

与 Milvus t_doc_collection 的 chunk 对应: 一个源文件一条记录(文档级),
chunk 实体在向量库。入库管道成功后由 pipeline 调用。
"""
from typing import Optional

from loguru import logger
from sqlalchemy import func, select

from models.ingestion import KnowledgeDocument
from ingestion.sync_db import SyncSession


def record_document(
    job_id: str,
    filename: str,
    user_id: Optional[int],
    file_md5: Optional[str],
    chunk_count: int,
    image_count: int,
    status: str = "ingested",
) -> None:
    """记录一个入库成功的文档(失败不入库, 失败详情看 ingest_jobs.error)。"""
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
