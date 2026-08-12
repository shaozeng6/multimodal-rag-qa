"""入库任务管理: MySQL 持久化(ingest_jobs 表) + daemon 线程执行。

管道是阻塞代码(OCR vllm HTTP / dashscope 嵌入 / Milvus 写入), 放 daemon 工作线程跑。
job 状态写入 MySQL(ingest_jobs), 重启后仍可查询历史(schema_v2 从 in-memory 迁移)。

schema_v3 增强(入库流程可视化):
- progress / stage_detail 由 pipeline 逐阶段上报
- 失败任务可复用同一 job_id 重试(临时 PDF 保留不删)
- running 任务协作式取消(阶段边界 + 向量化循环内检查, 阻塞 HTTP 调用内不打断)
- 每任务内存日志环形缓冲, get_job 返回尾部供前端日志抽屉展示
"""
import os
import shutil
import threading
import time
import uuid
from collections import deque
from typing import List, Optional

from loguru import logger
from sqlalchemy import func, select

from core.config import settings
from ingestion.sync_db import SyncSession
from models.ingestion import IngestJob


class JobCancelled(Exception):
    """入库任务被用户取消(协作式, 由 pipeline 在检查点抛出)。"""


# ========= 内存态: 取消标记 + 暂停/继续 + 日志缓冲(不进 MySQL, 重启即失) =========
_cancelled: set = set()
_cancel_lock = threading.Lock()
# 暂停: 任务在阶段边界等待, resume_job 放行; 上传勾选"先不处理"即置为暂停
_paused: set = set()
_resume_events: dict = {}
_resume_lock = threading.Lock()
_logs: dict = {}
_logs_lock = threading.Lock()
LOG_TAIL = 100  # 前端展示的日志条数
LOG_MAXLEN = 200  # 内存缓冲上限


def is_paused(job_id: str) -> bool:
    return job_id in _paused


def pause_job(job_id: str) -> bool:
    """暂停任务: 当前阶段结束后在下一个阶段边界停下。返回是否生效。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None or job.status not in ("running", "pending"):
            return False
    with _resume_lock:
        _paused.add(job_id)
    log_job(job_id, "已发送暂停请求, 本阶段结束后暂停")
    return True


def resume_job(job_id: str) -> bool:
    """继续任务: 清暂停标记并放行等待中的线程, 之后一路跑完剩余阶段。"""
    with _resume_lock:
        if job_id not in _paused:
            return False
        _paused.discard(job_id)
        ev = _resume_events.get(job_id)
        if ev is not None:
            ev.set()
    log_job(job_id, "继续执行")
    return True


def wait_if_paused(job_id: str) -> None:
    """阶段边界门: 任务被暂停时阻塞等待 resume(循环内响应取消)。"""
    if job_id not in _paused:
        return
    with _resume_lock:
        ev = _resume_events.setdefault(job_id, threading.Event())
        ev.clear()
    log_job(job_id, "已暂停, 等待继续")
    while job_id in _paused:
        if is_cancelled(job_id):
            raise JobCancelled
        ev.wait(timeout=0.5)
    log_job(job_id, "继续执行")
    with _resume_lock:
        _resume_events.pop(job_id, None)


def is_cancelled(job_id: str) -> bool:
    """pipeline 在阶段边界/向量化循环内检查。"""
    with _cancel_lock:
        return job_id in _cancelled


def log_job(job_id: str, msg: str) -> None:
    """向任务日志缓冲追加一行(带时间戳)。"""
    with _logs_lock:
        buf = _logs.get(job_id)
        if buf is None:
            buf = deque(maxlen=LOG_MAXLEN)
            _logs[job_id] = buf
        buf.append(f"{time.strftime('%H:%M:%S')} {msg}")


def _job_log_tail(job_id: str) -> List[str]:
    with _logs_lock:
        buf = _logs.get(job_id)
        if not buf:
            return []
        return list(buf)[-LOG_TAIL:]


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
        "progress": job.progress if job.progress is not None else 0,
        "stage_detail": job.stage_detail or "",
        "run_mode": job.run_mode or "auto",
        "phase": job.phase or "parse",
        "paused": job.id in _paused,
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
        "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
        "log": _job_log_tail(job.id),
    }


def create_job(filename: str, user_id: Optional[int] = None, run_mode: str = "auto",
               job_id: Optional[str] = None) -> str:
    """创建 pending 任务, 返回 job_id。

    job_id 由调用方传入时使用之(保证与 PDF 文件名等外部命名一致), 否则自生成。
    run_mode=manual: 上传后停驻(不启动线程), 由管理员分别点「解析」「入库」触发两段,
    不设置自动暂停(_paused)——手动触发本身就是控制, 否则 _run_parse 会在首阶段边界等"继续"卡住。
    """
    job_id = job_id or uuid.uuid4().hex[:12]
    with SyncSession() as db:
        db.add(IngestJob(
            id=job_id, filename=filename, user_id=user_id,
            status="pending", stage="等待执行", progress=0, stage_detail="",
            run_mode="manual" if run_mode == "manual" else "auto",
            phase="parse",
        ))
        db.commit()
    if run_mode == "manual":
        logger.info("[入库] 任务 {} 以停驻态创建(手动控制)", job_id)
    return job_id


def update_job(job_id: str, **fields) -> None:
    """更新任务字段(status/stage/progress/error 等)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if not job:
            return
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        db.commit()


def get_job(job_id: str) -> Optional[dict]:
    """查询任务(含进度/阶段明细/日志尾部)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        return _job_to_dict(job) if job else None


def count_jobs_by_status(status: str) -> int:
    """按状态统计任务数(供 /knowledge/status)。"""
    with SyncSession() as db:
        return db.execute(
            select(func.count()).select_from(IngestJob).where(IngestJob.status == status)
        ).scalar_one() or 0


def list_jobs(limit: int = 20, status: Optional[str] = None) -> List[dict]:
    """最近任务列表(按创建时间倒序), 可按 status 筛选。"""
    with SyncSession() as db:
        q = select(IngestJob).order_by(IngestJob.created_at.desc())
        if status:
            q = q.where(IngestJob.status == status)
        rows = db.execute(q.limit(limit)).scalars().all()
        return [_job_to_dict(j) for j in rows]


def _cleanup_intermediate(job_id: str, keep_chunks: bool = False) -> None:
    """清理该任务的中间产物: OCR md/json/jpg + 临时 PDF。

    keep_chunks=True 时保留 chunks.json(解析产物, 供重索引复用); 图片目录(md5 共享)不删。
    """
    out_dir = os.path.join(settings.INGEST_OUTPUT_DIR, job_id)
    if os.path.isdir(out_dir):
        if keep_chunks:
            for name in os.listdir(out_dir):
                if name == "chunks.json":
                    continue
                p = os.path.join(out_dir, name)
                if os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
            try:
                os.rmdir(out_dir)  # 仅当目录已空(无 chunks.json 残留)才成功
            except OSError:
                pass
        else:
            shutil.rmtree(out_dir, ignore_errors=True)
        logger.info("[入库] 已清理 OCR 中间产物: {}", out_dir)
    tmp_pdf = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")
    if os.path.isfile(tmp_pdf):
        os.remove(tmp_pdf)
        logger.info("[入库] 已清理临时 PDF: {}", tmp_pdf)


def _run_job(job_id: str, pdf_path: str, filename: str, user_id: Optional[int],
             run_mode: str = "auto") -> None:
    """daemon 线程执行入库管道(解析 → 索引)。延迟导入 pipeline 避免循环依赖。

    解析产物 chunks.json 存在则跳过解析直接索引(复用); 否则先解析后索引。
    """
    from ingestion.pipeline import chunks_path, index_document, parse_document

    chunks_file = chunks_path(job_id)
    already_parsed = os.path.isfile(chunks_file)
    log_job(job_id, f"任务开始: {filename} (运行模式: {'先不处理, 稍后开始' if run_mode == 'manual' else '自动'}"
                    f", 已解析: {'是' if already_parsed else '否'})")
    update_job(job_id, status="running", stage="启动", progress=0, error=None,
               phase="index" if already_parsed else "parse")
    try:
        file_size = os.path.getsize(pdf_path) if os.path.isfile(pdf_path) else 0
        if already_parsed:
            # 复用解析产物, 只跑索引段
            update_job(job_id, phase="index")
            count = index_document(job_id, filename, user_id, file_size)
        else:
            update_job(job_id, phase="parse")
            parse_document(pdf_path, filename, job_id)
            update_job(job_id, phase="index")
            count = index_document(job_id, filename, user_id, file_size)
        update_job(job_id, status="success", stage="完成", progress=100, documents_count=count)
        log_job(job_id, f"任务完成, 入库 {count} 条")
        logger.info("[入库] job {} 完成, 入库 {} 条", job_id, count)
        # 成功后清理 OCR md/json/jpg + 临时 PDF; 保留 chunks.json(解析资产, 供重索引)
        _cleanup_intermediate(job_id, keep_chunks=True)
    except JobCancelled:
        update_job(job_id, status="cancelled", stage="已取消", progress=None)
        log_job(job_id, "任务已取消, 清理中间产物")
        logger.info("[入库] job {} 已取消", job_id)
        _cleanup_intermediate(job_id, keep_chunks=True)
    except Exception as e:
        logger.exception("[入库] job {} 失败: {}", job_id, e)
        update_job(job_id, status="error", stage="失败", error=str(e))
        log_job(job_id, f"任务失败: {e}")
        # 失败不清理: chunks.json(已解析) 供重试复用索引, 临时 PDF 供重试复用解析
    finally:
        with _cancel_lock:
            _cancelled.discard(job_id)
        with _resume_lock:
            _paused.discard(job_id)
            _resume_events.pop(job_id, None)


def start_job(pdf_path: str, filename: str, user_id: Optional[int] = None,
              run_mode: str = "auto", job_id: Optional[str] = None) -> str:
    """创建任务, 返回 job_id。

    job_id 须与上传侧生成的一致(pdf_path 已按该 id 命名), 避免 DB 任务 id 与磁盘文件名错位。
    run_mode=auto 立即后台执行(解析→索引一段跑完);
    run_mode=manual 仅上传停住(不启动), 由管理员分别点击「解析」「入库」触发两段。
    """
    job_id = create_job(filename, user_id, run_mode=run_mode, job_id=job_id)
    if run_mode == "manual":
        log_job(job_id, "已上传, 手动控制: 点击「解析」开始 OCR 识别")
        return job_id
    _spawn(job_id, pdf_path, filename, user_id, _run_job, run_mode)
    return job_id


def _finish_locks(job_id: str) -> None:
    """清理线程退出时的取消/暂停内存态。"""
    with _cancel_lock:
        _cancelled.discard(job_id)
    with _resume_lock:
        _paused.discard(job_id)
        _resume_events.pop(job_id, None)


def _run_parse(job_id: str, pdf_path: str, filename: str, user_id: Optional[int],
               run_mode: str = "manual") -> None:
    """手动模式: 只跑解析段(OCR/分片)。成功后停在「解析完成, 等待入库」。"""
    from ingestion.pipeline import parse_document

    log_job(job_id, "手动解析开始")
    update_job(job_id, status="running", phase="parse", stage="启动", progress=0, error=None)
    try:
        parse_document(pdf_path, filename, job_id)
        update_job(job_id, status="pending", phase="parsed", stage="解析完成",
                   stage_detail="等待入库", progress=100)
        log_job(job_id, "解析完成, 等待入库")
        logger.info("[入库] job {} 解析完成, 等待入库", job_id)
    except JobCancelled:
        update_job(job_id, status="cancelled", stage="已取消", progress=None)
        log_job(job_id, "解析被取消")
        _cleanup_intermediate(job_id, keep_chunks=False)
    except Exception as e:
        logger.exception("[入库] job {} 解析失败: {}", job_id, e)
        update_job(job_id, status="error", stage="解析失败", error=str(e))
        log_job(job_id, f"解析失败: {e}")
    finally:
        _finish_locks(job_id)


def _run_index(job_id: str, pdf_path: str, filename: str, user_id: Optional[int],
               run_mode: str = "manual") -> None:
    """手动模式: 只跑索引段(描述/向量化/入库)。"""
    from ingestion.pipeline import index_document

    log_job(job_id, "手动入库开始")
    update_job(job_id, status="running", phase="index", stage="启动", progress=0, error=None)
    try:
        file_size = os.path.getsize(pdf_path) if (pdf_path and os.path.isfile(pdf_path)) else 0
        count = index_document(job_id, filename, user_id, file_size)
        update_job(job_id, status="success", phase="index", stage="完成",
                   progress=100, documents_count=count)
        log_job(job_id, f"入库完成, 共 {count} 条")
        logger.info("[入库] job {} 入库完成, {} 条", job_id, count)
        _cleanup_intermediate(job_id, keep_chunks=True)
    except JobCancelled:
        update_job(job_id, status="cancelled", stage="已取消", progress=None)
        log_job(job_id, "入库被取消")
        _cleanup_intermediate(job_id, keep_chunks=True)
    except Exception as e:
        logger.exception("[入库] job {} 索引失败: {}", job_id, e)
        update_job(job_id, status="error", stage="索引失败", error=str(e))
        log_job(job_id, f"索引失败: {e}")
    finally:
        _finish_locks(job_id)


def start_parse(job_id: str) -> bool:
    """手动触发解析段(仅当任务处于「待解析」状态)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            return False
        phase = job.phase or "parse"  # 兼容 phase 列迁移前的存量任务(NULL 视为 parse)
        if job.status != "pending" or phase != "parse":
            raise ValueError(f"任务当前状态不可开始解析 (status={job.status}, phase={phase})")
        filename, user_id = job.filename, job.user_id
    pdf_path = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")
    if not os.path.isfile(pdf_path):
        raise ValueError(f"源 PDF 已不存在: {pdf_path}, 请重新上传")
    _spawn(job_id, pdf_path, filename, user_id, _run_parse)
    log_job(job_id, "已触发手动解析")
    logger.info("[入库] 手动触发解析: {}", job_id)
    return True


def start_index(job_id: str) -> bool:
    """手动触导入库段(仅当解析完成、处于「待入库」状态)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            return False
        phase = job.phase or "parse"
        if job.status != "pending" or phase != "parsed":
            raise ValueError(f"任务当前状态不可入库(需先解析完成) (status={job.status}, phase={phase})")
        filename, user_id = job.filename, job.user_id
    _spawn(job_id, None, filename, user_id, _run_index)
    log_job(job_id, "已触导入库")
    logger.info("[入库] 手动触导入库: {}", job_id)
    return True


def retry_job(job_id: str) -> Optional[str]:
    """重试失败任务: 复用同一 job_id, 重置状态后重新入队。返回 job_id。

    前提: 任务 status==error 且临时 PDF 仍存在(失败任务的磁盘产物不清理)。
    """
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            return None
        if job.status != "error":
            raise ValueError("仅失败任务可重试")
        filename = job.filename
        user_id = job.user_id
        run_mode = job.run_mode or "auto"
        phase = job.phase or "parse"
    pdf_path = os.path.join(settings.INGEST_TMP_DIR, f"{job_id}.pdf")
    if not os.path.isfile(pdf_path):
        raise ValueError("任务源 PDF 已不存在(已被清理), 无法重试, 请重新上传")
    chunks_file = os.path.join(settings.INGEST_OUTPUT_DIR, job_id, "chunks.json")
    if run_mode == "auto":
        # 自动: _run_job 按 chunks.json 决定重解析还是重索引
        update_job(job_id, status="pending", stage="等待执行", progress=0, stage_detail="",
                   error=None, documents_count=None,
                   phase="index" if os.path.isfile(chunks_file) else "parse")
        _spawn(job_id, pdf_path, filename, user_id, _run_job, run_mode)
    elif phase == "index" or os.path.isfile(chunks_file):
        # 手动·索引段失败: 复用解析产物, 只重跑索引
        update_job(job_id, status="pending", phase="parsed", stage="解析完成",
                   stage_detail="等待入库", progress=100, error=None, documents_count=None)
        _spawn(job_id, None, filename, user_id, _run_index, run_mode)
    else:
        # 手动·解析段失败: 清旧 OCR 半成品, 重解析
        out_dir = os.path.join(settings.INGEST_OUTPUT_DIR, job_id)
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
            logger.info("[入库] 重试前清理旧 OCR 产物: {}", out_dir)
        update_job(job_id, status="pending", phase="parse", stage="等待执行", progress=0,
                   stage_detail="", error=None, documents_count=None)
        _spawn(job_id, pdf_path, filename, user_id, _run_parse, run_mode)
    logger.info("[入库] 任务重试: {} (run_mode={}, phase={})", job_id, run_mode, phase)
    return job_id


def remove_job(job_id: str) -> bool:
    """删除任务记录并清理其中间产物。仅允许 error/cancelled 终态(删除已失败/已放弃的任务)。"""
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            return False
        if job.status not in ("error", "cancelled"):
            raise ValueError("仅可删除失败/已取消的任务")
        db.delete(job)
        db.commit()
    _cleanup_intermediate(job_id)
    logger.info("[入库] 任务已删除并清理: {}", job_id)
    return True


def cancel_job(job_id: str) -> bool:
    """取消任务。

    - running: 协作式(设置标记, pipeline 阶段边界/向量化循环内生效)
    - pending(手动停驻, 无线程): 直接置为已取消, 并清理中间产物
    """
    with SyncSession() as db:
        job = db.get(IngestJob, job_id)
        if job is None:
            return False
        if job.status == "pending":
            # 手动停驻任务(待解析/待入库): 没有线程在跑, 直接终态化
            job.status = "cancelled"
            job.stage = "已取消"
            job.stage_detail = ""
            db.commit()
            logger.info("[入库] 停驻任务直接取消: {}", job_id)
            return True
        if job.status != "running":
            return False
    with _cancel_lock:
        _cancelled.add(job_id)
    log_job(job_id, "已收到取消请求, 等待当前阶段结束")
    logger.info("[入库] 任务取消请求: {}", job_id)
    return True


def _spawn(job_id: str, pdf_path: str, filename: str, user_id: Optional[int],
           runner=_run_job, run_mode: str = "auto") -> None:
    """在 daemon 线程执行 runner(job_id, pdf_path, filename, user_id, run_mode)。"""
    t = threading.Thread(
        target=runner, args=(job_id, pdf_path, filename, user_id, run_mode), daemon=True
    )
    t.start()
