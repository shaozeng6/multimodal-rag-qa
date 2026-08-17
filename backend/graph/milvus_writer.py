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

from infra.config import CONTEXT_COLLECTION_NAME
from infra.embedding import embedding
from infra.milvus import milvus_client
from services.config_service import get_int

# 全局线程池用于异步操作
thread_pool = ThreadPoolExecutor(max_workers=5)

# 每用户记忆条数上限(超限惰性淘汰最旧; sys_config hot 覆盖)
MEMORY_MAX_PER_USER = 500

# 回答里的来源引用标记(如 [检索内容1]): 作为记忆回喂时会与当前检索编号错位, 写入前剥离
# \s* 连标记前紧贴的空白一起吃掉, 避免残留多余空格(不折叠正文内合法的换行)
_CITE_RE = re.compile(r"\s*\[检索内容\d+\]")


def _hit_ts(hit: dict) -> Optional[int]:
    """从查询命中提取 timestamp(容错字符串/缺失)。"""
    ts = hit.get("timestamp")
    if ts is None:
        return None
    try:
        return int(ts)
    except (TypeError, ValueError):
        return None


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

    def _enforce_user_cap(self, user: str) -> None:
        """每用户记忆上限惰性淘汰(memory.max_per_user): 超限删该用户最旧条目。

        写入线程内同步调用; 失败只告警, 不影响本次写入主流程。
        """
        max_per_user = get_int("memory.max_per_user", MEMORY_MAX_PER_USER)
        if not user or max_per_user <= 0:
            return
        try:
            hits = self.client.query(
                collection_name=self.collection_name,
                filter='user == "{}"'.format(user.replace('"', "")),
                output_fields=["id", "timestamp"],
                limit=max_per_user + 1,
            )
        except Exception as e:
            logger.warning("[Milvus] 查询用户记忆数量失败(跳过本次淘汰): {}", e)
            return
        if len(hits) <= max_per_user:
            return
        # 按时间升序取最旧的超量部分删除
        ordered = sorted(hits, key=lambda h: _hit_ts(h) or 0)
        excess = ordered[: len(hits) - max_per_user]
        ids = [int(h["id"]) for h in excess if h.get("id") is not None]
        if not ids:
            return
        try:
            self.client.delete(collection_name=self.collection_name, ids=ids)
            logger.info("[Milvus] 记忆淘汰: 用户 {} 超出上限({}), 删除 {} 条最旧记忆",
                        user, max_per_user, len(ids))
        except Exception as e:
            logger.warning("[Milvus] 记忆淘汰失败(不影响本次写入): {}", e)

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
        # 惰性淘汰: 每用户超上限删最旧(与写入同线程, 失败只告警)
        user_str = str(user) if user is not None else ""
        await asyncio.get_running_loop().run_in_executor(
            thread_pool, self._enforce_user_cap, user_str
        )


def purge_expired_memories(ttl_days: Optional[int] = None) -> int:
    """清理超过 TTL 的记忆条目(硬 TTL 的后台回收, 全用户)。

    检索侧 `_search_context` 已用 `timestamp > now - ttl` 过滤, 这里回收存储:
    删 `timestamp < now - ttl` 的条目。启动时与后台每日任务调用; 失败只告警。
    """
    ttl_days = ttl_days or get_int("memory.ttl_days", MEMORY_TTL_DAYS)
    if not ttl_days or ttl_days <= 0:
        return 0
    cutoff_ms = int(time.time() * 1000) - ttl_days * 86400 * 1000
    try:
        result = milvus_client.delete(
            collection_name=CONTEXT_COLLECTION_NAME,
            filter="timestamp < {}".format(cutoff_ms),
        )
        deleted = int(result.get("delete_count", 0) or 0)
        if deleted:
            logger.info("[Milvus] 记忆清理: 删除 {} 条超过 {} 天 TTL 的过期记忆", deleted, ttl_days)
        return deleted
    except Exception as e:
        logger.warning("[Milvus] 记忆清理失败: {}", e)
        return 0


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
