"""入库模型: ingest_jobs / knowledge_documents。

schema_v2 规范化: 入库任务从 in-memory dict 持久化到 MySQL, 重启可查历史;
知识文档元数据独立成表, 与 Milvus t_doc_collection 的 chunk 对应(文档级记录)。
"""
from datetime import datetime

from sqlalchemy import Column, Integer, BigInteger, String, Enum, Text, DateTime, ForeignKey

from db.mysql import Base


class IngestJob(Base):
    """入库任务表: 上传 PDF → OCR → 分片 → 向量化 → Milvus 的整个生命周期。"""

    __tablename__ = "ingest_jobs"

    id = Column(String(12), primary_key=True)  # job_id(uuid hex 前12位)
    filename = Column(String(255), nullable=False)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(Enum("pending", "running", "success", "error"), default="pending")
    stage = Column(String(50), default="等待执行")
    documents_count = Column(Integer)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeDocument(Base):
    """知识文档元数据: 每个上传的源文件一条记录, chunk 实体在 Milvus t_doc_collection。"""

    __tablename__ = "knowledge_documents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(
        String(12),
        ForeignKey("ingest_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    filetype = Column(String(20), default="pdf")
    title = Column(String(255), default="")
    status = Column(Enum("ingested", "partial", "failed", "deleted"), default="ingested")
    chunk_count = Column(Integer, default=0)
    image_count = Column(Integer, default=0)
    file_md5 = Column(String(32), index=True)  # 源文件去重
    uploaded_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
