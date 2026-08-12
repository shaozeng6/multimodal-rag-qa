"""Milvus 异步写入器: 把高质量 AI 回答写入检索记忆集合(t_context_collection)。

从 nodes.py 拆分: 记忆落库是独立的横切关注点, 单独成模块便于复用与测试。
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

from loguru import logger
from pymilvus import MilvusClient

from graph.llm_init import (
    CONTEXT_COLLECTION_NAME,
    embedding,
    milvus_client,
)

# 全局线程池用于异步操作
thread_pool = ThreadPoolExecutor(max_workers=5)


class OptimizedMilvusAsyncWriter:
    """异步写入上下文到 Milvus(把最终 AI 回答保存到上下文向量数据库)。"""

    def __init__(self,
                 client: MilvusClient,
                 collection_name: str = "t_context_collection"):
        self.client = client
        self.collection_name = collection_name

    def _get_dense_vector(self, text: str):
        """生成稠密向量(同步, 在 run_in_executor 线程中执行)。"""
        try:
            return embedding.embed_query(text)
        except Exception as e:
            logger.exception("向量生成失败: {}", e)
            return None

    def _sync_insert(self, data: Dict):
        """同步插入数据到 Milvus。"""
        try:
            result = self.client.insert(collection_name=self.collection_name, data=data)
            logger.info("[Milvus] 成功插入 {} 条记录。IDs 示例: {}",
                        result['insert_count'], result['ids'][:5])
        except Exception as e:
            logger.exception("插入数据到 Milvus 失败: {}", e)

    async def async_insert(self, context_text: str, user: object, message_type: str = "AIMessage"):
        """异步插入数据。

        user 传数字 user_id(记忆按 id 隔离, 改名不影响归属); VARCHAR 字段存其字符串形式。
        """
        # 向量生成放线程池, 避免阻塞事件循环
        dense_vector = await asyncio.get_running_loop().run_in_executor(
            thread_pool, self._get_dense_vector, context_text
        )
        data = {
            "context_text": context_text,
            "user": str(user) if user is not None else None,
            "timestamp": int(time.time() * 1000),  # 毫秒时间戳
            "message_type": message_type,
            "context_dense": dense_vector,
        }
        await asyncio.get_running_loop().run_in_executor(thread_pool, self._sync_insert, data)


# 全局写入器实例(单例模式)
_milvus_writer_instance = None


def get_milvus_writer() -> OptimizedMilvusAsyncWriter:
    """获取全局 Milvus 写入器实例(单例)。"""
    global _milvus_writer_instance
    if _milvus_writer_instance is None:
        _milvus_writer_instance = OptimizedMilvusAsyncWriter(
            client=milvus_client,
            collection_name=CONTEXT_COLLECTION_NAME,
        )
    return _milvus_writer_instance
