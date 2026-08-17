"""文本 Embedding 实例(从 graph/llm_init.py 拆分, 2026-08 架构整理)。

记忆写入/检索与知识库语义切分共用此实例(OpenAIEmbeddings, qwen3.7-text-embedding)。
注意: 向量维数须与 Milvus 集合 dense 字段一致(infra.config.EMBEDDING_DIMENSIONS)。
"""
from langchain_openai import OpenAIEmbeddings

from infra.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
)

embedding = OpenAIEmbeddings(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    model=EMBEDDING_MODEL,
    dimensions=EMBEDDING_DIMENSIONS,
    check_embedding_ctx_length=False,  # 关键参数
)
