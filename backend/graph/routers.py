"""LangGraph 工作流路由函数。

新流程不再区分"历史优先/知识库兜底"两条生成路径(统一由 generator_node 生成),
路由只负责评估与人工审批的分流。
"""
from loguru import logger

from core.config import settings
from graph.state import MultiModalRAGState
from services.config_service import get_float


def route_image_analysis(state: MultiModalRAGState):
    """process_input 后分流: 有图才做图片理解(纯图/图文), 纯文本直接进改写。

    纯文本轮的 caption/relation 残留由 process_input 每轮清空, 不依赖本节点。
    """
    if state.get("input_image"):
        return "image_analysis"
    return "query_rewriter"


def route_human_node(state: MultiModalRAGState):
    """
    评估后的路由(Phase C):
    - evaluate_score 为 None(评估失败/无回答): 静默放行, 不打扰用户
    - score >= 阈值(默认 0.7, 可配置): 通过, 持久化
    - score < 阈值: 按角色分流——
      管理员 → 进入人工审批中断(中断等 admin 通过/驳回)
      普通用户 → 不打断, 直接持久化交付(由 persist_context 标记 needs_review 待管理端审核)
    """
    score = state.get("evaluate_score")
    if score is None:
        logger.warning("[路由] 评估失败或未评分, 静默放行 → persist_context")
        return "persist_context"

    # 阈值从 sys_config 读(hot 生效), settings.EVALUATE_THRESHOLD 作兜底
    threshold = get_float("evaluate.threshold", settings.EVALUATE_THRESHOLD)
    if score >= threshold:
        logger.info("[路由] evaluate_node → persist_context (score={:.3f} >= {:.2f})", score, threshold)
        return "persist_context"

    if state.get("role") == "admin":
        logger.info("[路由] evaluate_node → human_approval (score={:.3f} < {:.2f}, 管理员)", score, threshold)
        return "human_approval"
    # 普通用户低分: 不打断, 直接交付, persist 标记待审
    logger.info("[路由] evaluate_node → persist_context (score={:.3f} < {:.2f}, 普通用户, 标记待审)",
                score, threshold)
    return "persist_context"


def route_human_approval_node(state: MultiModalRAGState):
    """
    人工审批后的路由:
    - approve: 通过, 持久化
    - reject: 重新生成(regenerate_node)
    """
    if state.get("human_answer") == "approve":
        logger.info("[路由] human_approval → persist_context (approve)")
        return "persist_context"
    logger.info("[路由] human_approval → regenerate_node (reject)")
    return "regenerate_node"
