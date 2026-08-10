"""LangGraph 工作流节点(聚合门面)。

新流程(Phase A+B):
  process_input → query_rewriter → retriever_node(统一) → generator_node
    → (纯图 或 score≥0.7) persist_context → END
    → evaluate_node → (score<0.7) human_approval[中断]
        → approve → persist_context → END
        → reject → regenerate_node → persist_context → END

上下文管理: messages(标准 BaseMessage 历史) + 滑动窗口 + 滚动摘要。
检索: 统一三路(文本/图片/记忆) + 去重→RRF→topK, 见 graph/retrieval.py。

本文件已拆分为职责模块, 仅作向后兼容的聚合导出(workflow_service 从本模块导入):
- nodes_input:   输入处理 / 图片理解 / 问题改写与拆分
- nodes_generate: 单一生成 / 人工审批 / 驳回重生成
- nodes_evaluate: LLM-as-Judge 评估
- nodes_persist:  轮末持久化(对话历史/摘要/MySQL/Milvus)
- nodes_shared:   图片进模型共享辅助与常量(generator/evaluate 共用)
- milvus_writer:  记忆 Milvus 异步写入器
"""
from graph.nodes_input import (
    process_input,
    image_analysis_node,
    query_rewriter_node,
)
from graph.nodes_generate import (
    generator_node,
    human_approval,
    regenerate_node,
)
from graph.nodes_evaluate import evaluate_answer
from graph.nodes_persist import persist_context_node
from graph.milvus_writer import OptimizedMilvusAsyncWriter, get_milvus_writer
from graph.retrieval import unified_retrieve

# 检索节点即统一检索函数(确定性节点, 非工具调用)
retriever_node = unified_retrieve

__all__ = [
    "process_input",
    "image_analysis_node",
    "query_rewriter_node",
    "retriever_node",
    "generator_node",
    "human_approval",
    "regenerate_node",
    "evaluate_answer",
    "persist_context_node",
    "OptimizedMilvusAsyncWriter",
    "get_milvus_writer",
]
