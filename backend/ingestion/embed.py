"""向量化与入库(复用 infra 的 dashscope 嵌入与 Milvus 客户端)。

保持对称性原则(见 KNOWN_ISSUES.md 设计决策区):
- 图片 → {"image": img} 纯视觉向量(与检索侧一致)
- 文本 → {"text": content} 文本向量
"""
import time
from typing import Dict, List, Optional

from loguru import logger

from infra.config import COLLECTION_NAME
from infra.dashscope import call_dashscope_once, normalize_image
from infra.milvus import milvus_client
from services.config_service import get_float, get_int

MAX_RETRIES = 3  # 默认值兜底, 运行时由 sys_config 覆盖
BASE_BACKOFF = 1.0  # 秒


def _retryable(status_code) -> bool:
    """向量化失败是否值得重试(D10 修复): 429 限流 / 5xx 服务端错误 / 调用异常(None)
    可重试; 400/403/404 等永久性失败不重试, 避免每项白等约 3s。"""
    return status_code is None or status_code == 429 or (status_code or 0) >= 500


def process_item(item: Dict) -> Optional[Dict]:
    """单个数据项向量化。图片走纯视觉嵌入(对称), 文本走文本嵌入。

    失败返回 None(调用方剔除该项), 不写空 dense: 空向量与维数不符会令 Milvus
    整批插入抛错, 使整个 job 判 error(数分钟 OCR 成果作废)。
    """
    max_retries = get_int("ingestion.embed_retries", MAX_RETRIES)
    base_backoff = get_float("ingestion.embed_backoff", BASE_BACKOFF)
    new_item = item.copy()
    raw_content = (new_item.get('text') or '').strip()
    image_raw = (new_item.get('image_path') or '').strip()

    if image_raw:
        img = normalize_image(image_raw)[0]
        input_data = [{"image": img}]  # 纯视觉, 保持入库/检索对称
    else:
        input_data = [{"text": raw_content}]

    ok, embedding, status_code, retry_after = call_dashscope_once(input_data)
    attempts = 1
    while not ok and attempts < max_retries and _retryable(status_code):
        # 429 时按 Retry-After 等待, 否则指数退避; 永久 4xx 不重试(D10)
        sleep_sec = retry_after or (base_backoff * (2 ** (attempts - 1)))
        logger.warning("[入库] 向量化失败(status={}), {}s 后第 {} 次重试",
                       status_code, round(sleep_sec, 2), attempts)
        time.sleep(sleep_sec)
        ok, embedding, status_code, retry_after = call_dashscope_once(input_data)
        attempts += 1

    if not ok:
        logger.warning("[入库] 向量化最终失败, 该项跳过: {}",
                       image_raw or raw_content[:60])
        return None
    new_item['dense'] = embedding
    return new_item


def write_to_milvus(processed_data: List[Dict]) -> List[int]:
    """写入 Milvus t_doc_collection, 返回插入实体的主键 ids(供文档级精确删除)。

    返回空列表表示无可写数据/未插入。ids 由 pipeline 传给 record_document 持久化,
    删除文档时按主键精确删, 同名重复上传互不影响。
    """
    if not processed_data:
        logger.warning("[入库] 无可写数据")
        return []
    # 防御: 剔除无向量项, 避免空向量令整批插入失败(与 process_item 契约一致)
    items = [it for it in processed_data if it.get("dense")]
    skipped = len(processed_data) - len(items)
    if skipped:
        logger.warning("[入库] 剔除 {} 条无向量项, 实际写入 {} 条", skipped, len(items))
    if not items:
        logger.warning("[入库] 无可写向量项")
        return []
    result = milvus_client.insert(collection_name=COLLECTION_NAME, data=items)
    ids = list(result.get('ids') or [])
    logger.info("[入库] Milvus 插入 {} 条, IDs 示例: {}",
                result.get('insert_count', 0), ids[:5])
    return ids


def delete_milvus_by_ids(ids: List[int]) -> int:
    """按主键删除 Milvus 实体(精确, 幂等: 不存在的 id 不报错)。"""
    if not ids:
        return 0
    result = milvus_client.delete(collection_name=COLLECTION_NAME, ids=ids)
    return int(result.get('delete_count', 0) or 0)


def query_milvus_ids_by_filename(filename: str) -> List[int]:
    """legacy 兜底: 按 filename 查询 Milvus 实体主键(供 milvus_ids 为 NULL 的存量文档删除)。

    仅当该 filename 在 knowledge_documents 中唯一时才由调用方放行(否则同名多文档会误删)。
    """
    try:
        expr = 'filename == "{}"'.format(filename.replace('"', ""))
        hits = milvus_client.query(
            collection_name=COLLECTION_NAME,
            filter=expr,
            output_fields=["id"],
        )
        return [int(h.get("id")) for h in hits if h.get("id") is not None]
    except Exception as e:
        logger.warning("[Milvus] 按 filename 查询 ids 失败: {}", e)
        return []


def _chunk_to_dict(h: dict) -> dict:
    """Milvus 命中实体 → chunk 展示 dict(image_path 由调用方 resolve 成 URL)。"""
    text = h.get("text") or ""
    return {
        "id": h.get("id"),
        "text": text,
        "category": h.get("category") or "unknown",
        "image_path": h.get("image_path") or None,
        "title": h.get("title") or "",
        "char_count": len(text),  # 展示用, 不落库; 图片 chunk = 描述长度
    }


def query_milvus_chunks_by_ids(ids: List[int]) -> List[dict]:
    """按主键查 chunk 明细, 保持 milvus_ids 的插入顺序(供文档 chunk 查看)。"""
    if not ids:
        return []
    expr = "id in [{}]".format(",".join(str(int(i)) for i in ids))
    try:
        hits = milvus_client.query(
            collection_name=COLLECTION_NAME,
            filter=expr,
            output_fields=["id", "text", "category", "image_path", "title"],
        )
    except Exception as e:
        logger.warning("[Milvus] 按 ids 查 chunk 失败: {}", e)
        return []
    id_to_hit = {h.get("id"): _chunk_to_dict(h) for h in hits}
    return [id_to_hit[i] for i in ids if i in id_to_hit]


def query_milvus_chunks_by_filename(filename: str) -> List[dict]:
    """legacy 兜底: 按 filename 查 chunk 明细(展示用, 同名多文档会并集)。"""
    try:
        expr = 'filename == "{}"'.format(filename.replace('"', ""))
        hits = milvus_client.query(
            collection_name=COLLECTION_NAME,
            filter=expr,
            output_fields=["id", "text", "category", "image_path", "title"],
        )
    except Exception as e:
        logger.warning("[Milvus] 按 filename 查 chunk 失败: {}", e)
        return []
    return [_chunk_to_dict(h) for h in hits]
