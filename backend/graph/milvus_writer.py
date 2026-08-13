"""Milvus 异步写入器: 把高质量 AI 问答对写入检索记忆集合(t_context_collection)。

从 nodes.py 拆分: 记忆落库是独立的横切关注点, 单独成模块便于复用与测试。
"""
import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from loguru import logger
from pymilvus import MilvusClient

from graph.llm_init import (
    CONTEXT_COLLECTION_NAME,
    embedding,
    milvus_client,
)

# 全局线程池用于异步操作
thread_pool = ThreadPoolExecutor(max_workers=5)

# 回答里的来源引用标记(如 [检索内容1]): 作为记忆回喂时会与当前检索编号错位, 写入前剥离
_CITE_RE = re.compile(r"\[检索内容\d+\]")


def _clean_memory_text(text: str) -> str:
    """剥离记忆文本中的引用标记, 保留答案正文。"""
    return _CITE_RE.sub("", text or "").strip()


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

    async def async_insert(
        self,
        context_text: str,
        user: object,
        message_type: str = "AIMessage",
        question: Optional[str] = None,
    ):
        """异步插入一条「问题 + 回答」记忆。

        user 传数字 user_id(记忆按 id 隔离, 改名不影响归属); VARCHAR 字段存其字符串形式。
        question 传用户问题(纯图等无文本时为空, 用回答本身兜底保证 question_dense 非空);
        context_text 存答案正文(剥离 [检索内容N] 引用标记, 避免回喂时编号错位)。
        """
        answer = _clean_memory_text(context_text)
        question = (question or "").strip() or answer

        # 两个稠密向量(问题 + 回答)放线程池, 避免阻塞事件循环
        def _vectors():
            return self._get_dense_vector(question), self._get_dense_vector(answer)

        question_dense, answer_dense = await asyncio.get_running_loop().run_in_executor(
            thread_pool, _vectors
        )
        data = {
            "question": question,
            "context_text": answer,
            "user": str(user) if user is not None else None,
            "timestamp": int(time.time() * 1000),  # 毫秒时间戳
            "message_type": message_type,
            "question_dense": question_dense,
            "context_dense": answer_dense,
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
