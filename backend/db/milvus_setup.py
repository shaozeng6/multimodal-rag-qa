"""Milvus 集合初始化(schema 自包含, 从旧项目 milvus_db/collections_operator.py 合并)。

适配企业版配置:
- 复用 graph/llm_init 的 milvus_client / COLLECTION_NAME / CONTEXT_COLLECTION_NAME / EMBEDDING_DIMENSIONS
- sparse 字段由 Milvus **BM25 Function** 从 text / context_text 自动生成, 写入侧不写 sparse
  (审计 C1/#10 的"写入侧缺 sparse"是误报——检索/写入代码与旧项目一致, sparse 是 auto-generated)
- create 幂等: 集合已存在则跳过; --force 才删除重建(会清空数据, 谨慎)

用法:
    python -m db.milvus_setup            # 确保两个集合存在(已存在则跳过)
    python -m db.milvus_setup --force    # 删除并重建(危险, 清空现有数据)
"""
import argparse
import sys

from loguru import logger
from pymilvus import DataType, Function, FunctionType

from graph.llm_init import (
    COLLECTION_NAME,
    CONTEXT_COLLECTION_NAME,
    EMBEDDING_DIMENSIONS,
    milvus_client,
)

# BM25 倒排索引参数(与旧项目一致)
_BM25_INDEX_PARAMS = {
    "inverted_index_algo": "DAAT_MAXSCORE",
    "bm25_k1": 1.2,   # TF 饱和度: 词频越高贡献越大, 控制一个词出现多少次才算"多"
    "bm25_b": 0.75,   # 文档长度归一化强度: 对长文档的惩罚强度
}
# 中文分词 analyzer(与旧项目一致; 检索端传原始文本 query 时按同一 analyzer 分词)
_ANALYZER_PARAMS = {"tokenizer": "jieba", "filter": ["cnalphanumonly"]}


def _t_doc_schema():
    """知识库集合 schema: 文本块/图片/表格统一入库, sparse 由 text 经 BM25 Function 生成。"""
    schema = milvus_client.create_schema()
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field(
        field_name="text", datatype=DataType.VARCHAR, max_length=6000,
        enable_analyzer=True, analyzer_params=_ANALYZER_PARAMS,
    )
    schema.add_field(field_name="category", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="filename", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="filetype", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="image_path", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field(field_name="dense", datatype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIMENSIONS)
    schema.add_function(Function(
        name="text_bm25_emb",
        input_field_names=["text"],      # 原始文本 VARCHAR 字段
        output_field_names=["sparse"],   # auto-generated 稀疏向量
        function_type=FunctionType.BM25,
    ))
    index_params = milvus_client.prepare_index_params()
    index_params.add_index(
        field_name="sparse", index_name="sparse_inverted_index",
        index_type="SPARSE_INVERTED_INDEX", metric_type="BM25", params=_BM25_INDEX_PARAMS,
    )
    index_params.add_index(
        field_name="dense", index_name="dense_inverted_index",
        index_type="AUTOINDEX", metric_type="IP",
    )
    return schema, index_params


def _t_context_schema():
    """跨会话记忆集合 schema: context_sparse 由 context_text 经 BM25 Function 生成。"""
    schema = milvus_client.create_schema()
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field(
        field_name="context_text", datatype=DataType.VARCHAR, max_length=6000,
        enable_analyzer=True, analyzer_params=_ANALYZER_PARAMS,
    )
    schema.add_field(field_name="user", datatype=DataType.VARCHAR, max_length=1000, nullable=True)
    schema.add_field(field_name="timestamp", datatype=DataType.INT64, nullable=True)
    schema.add_field(field_name="message_type", datatype=DataType.VARCHAR, max_length=100, nullable=True)
    schema.add_field(field_name="context_sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field(field_name="context_dense", datatype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIMENSIONS)
    schema.add_function(Function(
        name="text_bm25_emb",
        input_field_names=["context_text"],
        output_field_names=["context_sparse"],
        function_type=FunctionType.BM25,
    ))
    index_params = milvus_client.prepare_index_params()
    index_params.add_index(
        field_name="context_sparse", index_name="context_sparse_inverted_index",
        index_type="SPARSE_INVERTED_INDEX", metric_type="BM25", params=_BM25_INDEX_PARAMS,
    )
    index_params.add_index(
        field_name="context_dense", index_name="context_dense_inverted_index",
        index_type="AUTOINDEX", metric_type="IP",
    )
    return schema, index_params


def _ensure_collection(name: str, build_schema, force: bool = False) -> None:
    """幂等创建单个集合; force=True 时删除重建(清空数据)。"""
    if milvus_client.has_collection(name):
        if force:
            logger.warning("--force: 删除并重建集合 {} (现有数据将清空)", name)
            milvus_client.drop_collection(name)
        else:
            logger.info("集合 {} 已存在, 跳过创建(用 --force 可删除重建)", name)
            return
    schema, index_params = build_schema()
    milvus_client.create_collection(collection_name=name, schema=schema, index_params=index_params)
    logger.info("集合 {} 创建完成", name)


def ensure_milvus_collections(force: bool = False) -> None:
    """确保知识库与记忆两个集合存在(schema 见 _t_doc_schema / _t_context_schema)。"""
    _ensure_collection(COLLECTION_NAME, _t_doc_schema, force=force)
    _ensure_collection(CONTEXT_COLLECTION_NAME, _t_context_schema, force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="确保 Milvus 集合存在(缺省跳过已存在集合)")
    parser.add_argument("--force", action="store_true", help="删除并重建集合(危险, 清空数据)")
    args = parser.parse_args()
    try:
        ensure_milvus_collections(force=args.force)
        for cname in (COLLECTION_NAME, CONTEXT_COLLECTION_NAME):
            desc = milvus_client.describe_collection(cname)
            logger.info("{}: {} 条实体, schema 字段 = {}", cname,
                        desc.get("num_entities", "?"),
                        [f["name"] for f in desc.get("fields", [])])
    except Exception as exc:
        logger.exception("Milvus 集合初始化失败: {}", exc)
        sys.exit(1)
