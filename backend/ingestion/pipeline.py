"""入库管道编排: PDF → OCR → 分片 → 描述 → 向量化 → Milvus。

阻塞式(OCR vllm HTTP / dashscope 嵌入 / Milvus 写入), 由 ingestion.jobs 放到 daemon 线程执行。
逐阶段更新 job stage, 供前端/接口轮询进度。
"""
import hashlib
import os
from typing import Optional

from loguru import logger

from core.config import settings
from dots_ocr.parser import do_parse
from graph import llm_init
from ingestion.jobs import update_job
from ingestion.documents import record_document
from ingestion.splitter import MarkdownDirSplitter
from ingestion.convert import doc_to_dict, generate_image_description, generate_table_description
from ingestion.embed import process_item, write_to_milvus


def _ensure_dirs() -> None:
    os.makedirs(settings.INGEST_OUTPUT_DIR, exist_ok=True)
    os.makedirs(settings.INGEST_IMAGES_DIR, exist_ok=True)
    os.makedirs(settings.INGEST_TMP_DIR, exist_ok=True)


def _md5_of(path: str) -> str:
    """源文件 MD5(文档去重用)。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_ingestion(
    pdf_path: str,
    filename: str,
    job_id: str,
    user_id: Optional[int] = None,
) -> int:
    """执行完整入库管道, 返回入库条数, 成功后记录文档元数据。"""
    _ensure_dirs()

    # ① OCR: PDF → md 目录(输出到 INGEST_OUTPUT_DIR/<pdf_stem>/)
    update_job(job_id, stage="OCR识别(vllm)")
    logger.info("[入库] OCR 开始: {}", pdf_path)
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

    # ② 分片
    update_job(job_id, stage="分片")
    splitter = MarkdownDirSplitter(
        embedding=llm_init.embedding,
        images_output_dir=settings.INGEST_IMAGES_DIR,
    )
    docs = splitter.process_md_dir(book_dir, filename)
    logger.info("[入库] 分片完成: {} 个文档", len(docs))

    # ③ 转换 + 描述(图片用多模态, 表格用文本 LLM)
    update_job(job_id, stage="生成图片/表格描述")
    items = doc_to_dict(docs)
    items = generate_image_description(items, llm_init.multiModal_llm, llm_init.image_to_base64)
    items = generate_table_description(items, llm_init.llm)
    logger.info("[入库] 转换完成: {} 条(含描述)", len(items))

    # ④ 向量化(图片纯视觉嵌入, 保持对称性); 失败项返回 None, 剔除不写空向量
    update_job(job_id, stage="向量化")
    processed = [it for it in (process_item(item) for item in items) if it]
    ok_count = len(processed)
    logger.info("[入库] 向量化完成: {}/{} 条成功", ok_count, len(items))

    # ⑤ 入库, 拿回 Milvus 主键 ids(供文档级精确删除)
    update_job(job_id, stage="写入 Milvus")
    insert_ids = write_to_milvus(processed)

    # ⑥ 记录文档元数据(schema_v2): 与 Milvus chunk 对应的文档级记录
    image_count = sum(1 for it in items if it.get("image_path"))
    record_document(
        job_id=job_id,
        filename=filename,
        user_id=user_id,
        file_md5=_md5_of(pdf_path),
        chunk_count=len(processed),
        image_count=image_count,
        status="ingested",
        milvus_ids=insert_ids,
    )

    logger.info("[入库] 管道完成: 共 {} 条, 向量化成功 {} 条", len(processed), ok_count)
    return len(processed)
