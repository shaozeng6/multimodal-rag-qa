"""入库管道: 分两段「解析 parse → 索引 index」, 解析产物 chunks.json 落盘可复用。

- parse_document: PDF → OCR → 分片 → doc_to_dict, 把 chunk 集(文本/图片/表格)写入
  INGEST_OUTPUT_DIR/<job_id>/chunks.json。产物含提取的图片(共享目录) + chunks.json。
  解析结果可复用: 换嵌入模型/重跑索引, 不必重新 OCR/分片。
- index_document: 读 chunks.json → 生成描述 → 向量化 → 写入 Milvus → 记录文档。
  失败重试只重跑本段, 不重 OCR。

阶段边界均检查暂停(wait_if_paused); 向量化循环内逐条响应暂停。
"""
import hashlib
import json
import os
from typing import Optional

from loguru import logger

from core.config import settings
from dots_ocr.parser import do_parse
from graph import llm_init
from ingestion.convert import doc_to_dict, generate_image_description, generate_table_description
from ingestion.documents import record_document
from ingestion.embed import process_item, write_to_milvus
from ingestion.jobs import JobCancelled, is_cancelled, log_job, update_job, wait_if_paused
from ingestion.splitter import MarkdownDirSplitter


def _ensure_dirs() -> None:
    os.makedirs(settings.INGEST_OUTPUT_DIR, exist_ok=True)
    os.makedirs(settings.INGEST_IMAGES_DIR, exist_ok=True)
    os.makedirs(settings.INGEST_TMP_DIR, exist_ok=True)


def chunks_path(job_id: str) -> str:
    """解析产物文件(含 meta + items), 供索引段读取/复用。"""
    return os.path.join(settings.INGEST_OUTPUT_DIR, job_id, "chunks.json")


def _md5_of(path: str) -> str:
    """源文件 MD5(文档去重用)。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_reporter(job_id: str):
    """构造阶段上报/取消/暂停的闭包集合(两个阶段共用)。"""
    def _stage(stage: str, detail: str, progress: int) -> None:
        update_job(job_id, stage=stage, stage_detail=detail, progress=progress)
        log_job(job_id, f"[{stage}] {detail}")

    def _tick(detail: str, progress: int) -> None:
        update_job(job_id, stage_detail=detail, progress=progress)

    def _check_cancel() -> None:
        if is_cancelled(job_id):
            raise JobCancelled

    def _wait_pause(stage: str, label: str) -> None:
        """阶段边界门: 先把 job.stage 指向下一阶段, 若被暂停则阻塞等「继续」。"""
        update_job(job_id, stage=stage, stage_detail=label, progress=None)
        _check_cancel()
        wait_if_paused(job_id)
        _check_cancel()

    def _check_pause() -> None:
        wait_if_paused(job_id)

    return _stage, _tick, _check_cancel, _wait_pause, _check_pause


def parse_document(pdf_path: str, filename: str, job_id: str) -> int:
    """阶段1 解析: PDF → OCR → 分片 → doc_to_dict, chunk 集落盘 chunks.json。

    Returns: 解析出的 chunk 数。
    """
    _ensure_dirs()
    _stage, _tick, _check_cancel, _wait_pause, _check_pause = _make_reporter(job_id)

    # ① OCR: PDF → md 目录(每页 md/json/jpg)
    _wait_pause("OCR识别(vllm)", "等待执行 OCR 识别")
    _check_cancel()
    _stage("OCR识别(vllm)", "OCR 解析中", 10)
    do_parse(
        input_path=pdf_path,
        output=settings.INGEST_OUTPUT_DIR,
        ip=settings.OCR_VLLM_IP,
        port=settings.OCR_VLLM_PORT,
        model_name=settings.OCR_VLLM_MODEL,
        num_thread=settings.INGEST_OCR_THREADS,
        dpi=settings.INGEST_OCR_DPI,
        no_fitz_preprocess=True,
    )

    pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
    book_dir = os.path.join(settings.INGEST_OUTPUT_DIR, pdf_stem)
    if not os.path.isdir(book_dir):
        raise RuntimeError(f"OCR 未生成输出目录: {book_dir}")
    _stage("OCR识别(vllm)", "OCR 完成", 20)

    # ② 分片
    _wait_pause("分片", "等待执行分片")
    _check_cancel()
    _stage("分片", "分片进行中", 30)
    splitter = MarkdownDirSplitter(
        embedding=llm_init.embedding,
        images_output_dir=settings.INGEST_IMAGES_DIR,
    )
    docs = splitter.process_md_dir(book_dir, filename)
    _stage("分片", f"分片完成 {len(docs)} 个文档", 40)

    # ③ 转换 → chunk 集, 落盘复用(含 meta: 源文件 md5/大小)
    _check_cancel()
    items = doc_to_dict(docs)
    payload = {
        "file_size": os.path.getsize(pdf_path),
        "file_md5": _md5_of(pdf_path),
        "items": items,
    }
    os.makedirs(os.path.dirname(chunks_path(job_id)), exist_ok=True)
    with open(chunks_path(job_id), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    log_job(job_id, f"解析完成, {len(items)} 个 chunk 已落盘 (chunks.json)")
    return len(items)


def index_document(job_id: str, filename: str, user_id: Optional[int], file_size: int = 0) -> int:
    """阶段2 索引: 读 chunks.json → 生成描述 → 向量化 → 写入 Milvus → 记录文档。

    Returns: 入库条数。
    """
    _stage, _tick, _check_cancel, _wait_pause, _check_pause = _make_reporter(job_id)

    # 读解析产物
    cp = chunks_path(job_id)
    if not os.path.isfile(cp):
        raise RuntimeError("缺少解析产物 chunks.json, 请先执行解析")
    with open(cp, "r", encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items") or []
    file_md5 = payload.get("file_md5")
    file_size = file_size or payload.get("file_size") or 0
    log_job(job_id, f"索引开始, 读取解析产物 {len(items)} 个 chunk")

    # ④ 描述(图片用多模态, 表格用文本 LLM)
    _wait_pause("生成图片/表格描述", "等待执行生成描述")
    _check_cancel()
    _stage("生成图片/表格描述", "生成描述中", 50)
    items = generate_image_description(items, llm_init.multiModal_llm, llm_init.image_to_base64)
    items = generate_table_description(items, llm_init.llm)
    _stage("生成图片/表格描述", f"描述完成, 共 {len(items)} 条", 60)

    # ⑤ 向量化(循环内逐条响应暂停/取消)
    _wait_pause("向量化", "等待执行向量化")
    _check_cancel()
    _stage("向量化", f"向量化 0/{len(items)}", 70)
    processed: list = []
    total = len(items)
    for i, item in enumerate(items, 1):
        _check_cancel()
        _check_pause()
        p = process_item(item)
        if p is None:
            skipped = item.get('image_path') or (item.get('text') or '')[:40]
            log_job(job_id, f"[向量化] 失败跳过: {skipped}")
        else:
            processed.append(p)
        _tick(f"向量化 {i}/{total}", 70 + int(20 * i / total))
    _stage("向量化", f"向量化完成 {len(processed)}/{total}", 90)

    # ⑥ 写入 Milvus + 记录文档元数据
    _wait_pause("写入 Milvus", "等待执行写入向量库")
    _check_cancel()
    _stage("写入 Milvus", "写入向量库", 95)
    insert_ids = write_to_milvus(processed)
    image_count = sum(1 for it in items if it.get("image_path"))
    total_chars = sum(len(it.get("text") or "") for it in items)
    record_document(
        job_id=job_id,
        filename=filename,
        user_id=user_id,
        file_md5=file_md5,
        chunk_count=len(processed),
        image_count=image_count,
        char_count=total_chars,
        file_size=file_size,
        status="ingested",
        milvus_ids=insert_ids,
    )
    _stage("写入 Milvus", f"写入 {len(insert_ids)} 条", 100)
    logger.info("[入库] 索引完成: job={}, 入库 {} 条, 字符数 {}", job_id, len(processed), total_chars)
    return len(processed)
