"""入库任务管理: MySQL 持久化(ingest_jobs 表) + daemon 线程执行。

管道是阻塞代码(OCR vllm HTTP / dashscope 嵌入 / Milvus 写入), 放 daemon 工作线程跑。
job 状态写入 MySQL(ingest_jobs), 重启后仍可查询历史(schema_v2 从 in-memory 迁移)。
同步引擎(pymysql)供 daemon 线程直接使用, 见 sync_db.py 说明。
"""
import threading
import uuid
from typing import List, Optional

from loguru import logger
from sqlalchemy import select

from models.ingestion import IngestJob
from ingestion.sync_db import SyncSession


def _job_to_dict(job: IngestJob) -> dict:
    """ORM 对象 → 前端可读 dict(时间格式化)。"""
    return {
        "id": job.id,
        "filename": job.filename,
        "user_id": job.user_id,
        "status": job.status,
        "stage": job.stage,
        "documents_count": job.documents_count,
        "error": job.error,
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
        "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
    }


def create_job(filename: str, user_id: Optional[int] = None) -> str:
    """创建 pending 任务, 返回 job_id。"""
    job_id = uuid.uuid4().hex[:12]
    with SyncSession() as db:
        db.add(IngestJob(
            id=job_id, filename=filename, user_id=user_id,
            status="pending", stage="等待执行",
        ))
        db.commit()
    return job_id


def update_job(job_id: str, **fields) -> None:
    """更新任务字段(status/stage/documents_count/error 等)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if not job:
            return
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        db.commit()


def get_job(job_id: str) -> Optional[dict]:
    """查询任务。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        return _job_to_dict(job) if job else None


def list_jobs(limit: int = 20) -> List[dict]:
    """最近任务列表(按创建时间倒序)。"""
    with SyncSession() as db:
        rows = (
            db.execute(
                select(IngestJob)
                .order_by(IngestJob.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_job_to_dict(j) for j in rows]


def _run_job(job_id: str, pdf_path: str, filename: str, user_id: Optional[int]) -> None:
    """daemon 线程执行入库管道。延迟导入 pipeline 避免循环依赖。"""
    from ingestion.pipeline import run_ingestion

    update_job(job_id, status="running", stage="启动")
    try:
        count = run_ingestion(pdf_path, filename, job_id, user_id)
        update_job(job_id, status="success", stage="完成", documents_count=count)
        logger.info("[入库] job {} 完成, 入库 {} 条", job_id, count)
    except Exception as e:
        logger.exception("[入库] job {} 失败: {}", job_id, e)
        update_job(job_id, status="error", stage="失败", error=str(e))


def start_job(pdf_path: str, filename: str, user_id: Optional[int] = None) -> str:
    """创建任务并立即后台执行, 返回 job_id。"""
    job_id = create_job(filename, user_id)
    t = threading.Thread(
        target=_run_job, args=(job_id, pdf_path, filename, user_id), daemon=True
    )
    t.start()
    return job_id
