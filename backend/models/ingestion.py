"""入库模型: ingest_jobs / knowledge_documents。

schema_v2 规范化: 入库任务从 in-memory dict 持久化到 MySQL, 重启可查历史;
知识文档元数据独立成表, 与 Milvus t_doc_collection 的 chunk 对应(文档级记录)。
"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Column, DateTime, Enum, ForeignKey, Integer, String, Text

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
    status = Column(Enum("pending", "running", "success", "error", "cancelled"), default="pending")
    stage = Column(String(50), default="等待执行")
    documents_count = Column(Integer)
    error = Column(Text)
    # schema_v3: 入库流程可视化(进度百分比 / 阶段明细, 由 pipeline 上报)
    progress = Column(Integer, default=0)  # 0~100
    stage_detail = Column(String(255), default="")  # 如 "向量化 45/120"
    # schema_v3: 运行模式 auto 自动全流程 / manual 手动分步(每阶段完成即停, 等手动推进)
    run_mode = Column(String(10), default="auto")
    # schema_v4: 当前阶段 parse 解析(OCR/分片) / index 索引(描述/向量化/入库); 解析产物 chunks.json 落盘可复用
    phase = Column(String(10), default="parse")
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
    # schema_v3: 文档级统计(入库成功后由 pipeline 写入, 供管理页展示/筛选)
    char_count = Column(Integer, default=0)  # 文本类 chunk 字符数合计(含图片/表格描述)
    file_size = Column(BigInteger, default=0)  # 源文件字节数(上传时记录)
    # Milvus t_doc 主键列表(insert 返回的 ids): 按文档精确删除用; 存量数据为 NULL
    milvus_ids = Column(JSON, nullable=True)
    file_md5 = Column(String(32), index=True)  # 源文件去重
    uploaded_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
