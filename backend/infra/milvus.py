"""Milvus 客户端与检索器(从 graph/llm_init.py 拆分, 2026-08 架构整理)。

全局 milvus_client 供检索/记忆写入/入库管道/建集合共用; MilvusRetriever 封装
知识库 t_doc 的稠密检索与混合检索(稠密 + BM25)。
"""
from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker

from infra.config import COLLECTION_NAME, MILVUS_URI

milvus_client = MilvusClient(uri=MILVUS_URI)


class MilvusRetriever:
    """Milvus 检索器：稠密检索 + 混合检索。"""

    def __init__(self, collection_name: str, milvus_client: MilvusClient, top_k: int = 3):
        self.collection_name = collection_name
        self.milvus_client = milvus_client
        self.top_k = top_k

    def dense_search(self, query_dense_embedding, limit=10):
        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        res = self.milvus_client.search(
            collection_name=self.collection_name,
            data=[query_dense_embedding],
            anns_field="dense",
            limit=limit,
            output_fields=["text", 'category', 'filename', 'image_path', 'title'],
            search_params=search_params,
        )
        return res[0]

    def hybrid_search(
            self,
            query_dense_embedding,
            query_sparse_embedding,
            sparse_weight=1.0,
            dense_weight=1.0,
            limit=10,
    ):
        filter_expr = None
        dense_search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        dense_req = AnnSearchRequest(
            [query_dense_embedding], "dense", dense_search_params, limit=limit, expr=filter_expr
        )
        sparse_search_params = {"metric_type": "BM25", 'params': {'drop_ratio_search': 0.2}}
        sparse_req = AnnSearchRequest(
            [query_sparse_embedding], "sparse", sparse_search_params, limit=limit, expr=filter_expr
        )
        rerank = WeightedRanker(sparse_weight, dense_weight)
        return self.milvus_client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[sparse_req, dense_req],
            ranker=rerank,  # 重排算法
            limit=limit,
            output_fields=["text", 'category', 'filename', 'image_path', 'title'],
        )[0]


# 全局检索器实例(知识库 t_doc)
m_re = MilvusRetriever(COLLECTION_NAME, milvus_client)
