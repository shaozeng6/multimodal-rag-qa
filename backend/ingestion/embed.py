"""向量化与入库(复用 graph/llm_init 的 dashscope 嵌入与 Milvus 客户端)。

保持对称性原则(见 KNOWN_ISSUES.md 设计决策区):
- 图片 → {"image": img} 纯视觉向量(与检索侧一致)
- 文本 → {"text": content} 文本向量
"""
import time
from typing import Dict, List, Optional

from loguru import logger

from graph.llm_init import (
    call_dashscope_once,
    normalize_image,
    milvus_client,
    COLLECTION_NAME,
)

MAX_RETRIES = 3
BASE_BACKOFF = 1.0  # 秒


def process_item(item: Dict) -> Optional[Dict]:
    """单个数据项向量化。图片走纯视觉嵌入(对称), 文本走文本嵌入。

    失败返回 None(调用方剔除该项), 不写空 dense: 空向量与维数不符会令 Milvus
    整批插入抛错, 使整个 job 判 error(数分钟 OCR 成果作废)。
    """
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
    while not ok and attempts < MAX_RETRIES:
        # 429 时按 Retry-After 等待, 否则指数退避
        sleep_sec = retry_after or (BASE_BACKOFF * (2 ** (attempts - 1)))
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


def write_to_milvus(processed_data: List[Dict]) -> None:
    """写入 Milvus t_doc_collection。"""
    if not processed_data:
        logger.warning("[入库] 无可写数据")
        return
    # 防御: 剔除无向量项, 避免空向量令整批插入失败(与 process_item 契约一致)
    items = [it for it in processed_data if it.get("dense")]
    skipped = len(processed_data) - len(items)
    if skipped:
        logger.warning("[入库] 剔除 {} 条无向量项, 实际写入 {} 条", skipped, len(items))
    if not items:
        logger.warning("[入库] 无可写向量项")
        return
    result = milvus_client.insert(collection_name=COLLECTION_NAME, data=items)
    logger.info("[入库] Milvus 插入 {} 条, IDs 示例: {}",
                result.get('insert_count', 0), (result.get('ids') or [])[:5])
