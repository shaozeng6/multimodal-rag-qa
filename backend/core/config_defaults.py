"""运行参数默认值清单(配置中心单一事实来源)。

**可视化配置的边界(勿过度):** 只收录「需要频繁调参、且真正接到了代码、改完即时/下次生效」的运行参数——
检索召回、评估阈值、分片粒度、上下文窗口、图片上限。
基础设施类配置(模型型号/温度/维度、OCR 线程/DPI、限流、描述字数)归属 .env, 不进配置中心。

说明文案面向管理员: 用业务语言讲清"这是什么、调大/调小会怎样", 不用内部实现名词。
值 = 改造前代码里的硬编码值, 保证迁移后行为与原来完全一致。
新增配置项在此登记后, 由 config_service.seed_if_empty() 首次启动时写入 sys_config 表。

group 分组(设置页左侧一级导航):
  ingestion 入库 / retrieval 检索 / evaluation 评估 / context 上下文 / rag 图片上限
section 子分组(组内逻辑分域):
  入库按管道序 分片 → 向量化; 检索按 候选数 → 融合; 评估按 阈值 → 维度 → 范围; 上下文/图片上限单子域。
apply_mode: hot=保存即生效(调用处每次 get_*())
"""
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ConfigItem:
    """单个配置项的元信息与默认值。"""

    value: object  # 默认值(种子写入时 str() 化)
    value_type: str  # str/int/float/bool
    group: str  # 一级分组
    section: str  # 组内子分组(逻辑分域)
    label: str  # 中文名
    description: str  # 说明(面向管理员)
    apply_mode: str = "hot"


DEFAULTS: Dict[str, ConfigItem] = {
    # ============ 入库(ingestion) ============
    # ---- 分片 ----
    "ingestion.chunk_size": ConfigItem(
        1000, "int", "ingestion", "chunk", "标题块切分阈值（字符）",
        "文档先按标题（一级/二级/三级标题）切分为块；单个标题块超过该字符数时，再按语义边界进一步切分。"
        "值越大，检索单元越大、上下文越完整但精度越低；值越小，检索更精准但上下文更碎片化。", "hot",
    ),
    # ---- 向量化 ----
    "ingestion.embed_retries": ConfigItem(
        3, "int", "ingestion", "embed", "向量化失败重试次数",
        "把文本/图片转换为向量失败时的最大重试次数（不含首次尝试）。网络波动或服务限流时自动重试，降低入库失败率。", "hot",
    ),
    "ingestion.embed_backoff": ConfigItem(
        1.0, "float", "ingestion", "embed", "向量化重试等待（秒）",
        "重试之间的基础等待时长，按指数递增（1 秒、2 秒、4 秒…）。服务返回限流建议时优先按其建议等待。", "hot",
    ),

    # ============ 检索(retrieval) ============
    # ---- 各通道候选数 ----
    "retrieval.text_topk": ConfigItem(
        6, "int", "retrieval", "candidate", "文本检索候选数",
        "按文本内容检索（语义相似 + 关键词匹配）时，单次召回的候选条数。值越大召回越全但噪声越多。", "hot",
    ),
    "retrieval.image_topk": ConfigItem(
        4, "int", "retrieval", "candidate", "图片检索候选数",
        "以图搜图（用户发送图片时按视觉相似度检索）的候选条数。用户文字与图片内容不相关时，图片路自动跳过。", "hot",
    ),
    "retrieval.memory_topk": ConfigItem(
        3, "int", "retrieval", "candidate", "历史记忆候选数",
        "跨会话历史记忆检索的候选条数。系统会回忆此前问答过的高质量回答作为参考。", "hot",
    ),
    # ---- 融合 ----
    "retrieval.context_topk": ConfigItem(
        8, "int", "retrieval", "fuse", "最终参考上下文条数（召回数量）",
        "多路检索结果融合后，最终送入回答模型的上下文条数（即召回数量）。值越大参考信息越多，但占用更多模型上下文。", "hot",
    ),
    "retrieval.rrf_k": ConfigItem(
        60, "int", "retrieval", "fuse", "多路结果融合平滑常数",
        "多路检索结果融合时的平滑参数。值越大，各候选之间的排名差距被稀释得越平缓。一般无需调整。", "hot",
    ),
    "retrieval.memory_weight": ConfigItem(
        0.8, "float", "retrieval", "fuse", "历史记忆融合权重",
        "历史记忆在多路融合中的权重（低于知识库的 1.0）。调低可减弱历史回答对当前回答的干扰。", "hot",
    ),
    # ---- 嵌入重试 ----
    "retrieval.embed_retries": ConfigItem(
        2, "int", "retrieval", "embed", "检索嵌入失败重试次数",
        "检索时的文本/图片向量化请求失败（限流或服务端错误）时自动重试的最大次数（不含首次尝试）。"
        "降低限流时检索通道被静默丢弃的概率。", "hot",
    ),
    "retrieval.embed_backoff": ConfigItem(
        1.0, "float", "retrieval", "embed", "检索嵌入重试等待（秒）",
        "重试之间的基础等待时长，按指数递增；服务返回限流建议（Retry-After）时优先按其建议等待。", "hot",
    ),

    # ============ 评估(evaluation) ============
    # ---- 阈值与兜底 ----
    "evaluate.threshold": ConfigItem(
        0.7, "float", "evaluation", "threshold", "回答通过阈值（评判分数线）",
        "系统对回答质量的自动评分通过线（0~1）。评分低于该值的回答转入人工审批；评估过程异常时不拦截。", "hot",
    ),
    # ---- 维度默认分 ----
    "evaluate.dim_default": ConfigItem(
        5, "int", "evaluation", "dims", "维度缺省分（0-10）",
        "评估模型未给出某一维度分数时，采用的默认分值（0~10）。", "hot",
    ),
    "evaluate.image_fidelity_default": ConfigItem(
        10, "int", "evaluation", "dims", "图文一致性缺省分（0-10）",
        "回答完全未涉及图片内容时，「图文一致性」维度的默认满分，避免把未描述图片误判为图文不符。", "hot",
    ),
    # ---- 评审范围 ----
    "evaluate.history_turns": ConfigItem(
        4, "int", "evaluation", "scope", "评审参考历史轮数",
        "自动评审时参考的最近对话轮数，用于判断回答是否延续历史上下文。值越大判断越准确，但更耗模型额度。", "hot",
    ),

    # ============ 上下文(context) ============
    # ---- 窗口与摘要 ----
    "context.window_turns": ConfigItem(
        8, "int", "context", "window", "对话保留轮数",
        "对话中保留的最近原文轮数，更早的内容会压成摘要。值越大保留的信息越完整，但更耗模型额度。", "hot",
    ),
    "context.summary_trigger_turns": ConfigItem(
        9, "int", "context", "window", "摘要触发轮数",
        "对话超过该轮数后，对更早的内容触发摘要压缩，控制上下文长度。", "hot",
    ),
    "context.summary_max_chars": ConfigItem(
        400, "int", "context", "window", "摘要最大字符数",
        "历史摘要压缩后的最大字符数，控制长期对话的上下文开销。", "hot",
    ),

    # ============ 图片上限(rag) ============
    # ---- 送模型图片上限 ----
    "rag.max_images_to_model": ConfigItem(
        4, "int", "rag", "images", "单轮图片总上限",
        "单轮问答送入模型的图片总数上限（含用户输入图与历史图），控制模型额度消耗。", "hot",
    ),
    "rag.retrieved_images_to_model": ConfigItem(
        2, "int", "rag", "images", "评审参考检索图数",
        "自动评审时最多参考的检索命中图片数，用于核实回答对知识库图片的引用是否属实。", "hot",
    ),
    "rag.history_images_to_model": ConfigItem(
        1, "int", "rag", "images", "评审参考历史图数",
        "自动评审时从历史对话还原的最多图片数，用于核实回答对历史图片的引用是否属实。", "hot",
    ),

    # ============ 记忆(memory) ============
    # ---- 时效与淘汰(2026-08: 修 KNOWN_ISSUES #4, 检索硬 TTL + 软衰减 + 每用户上限) ----
    "memory.ttl_days": ConfigItem(
        180, "int", "memory", "policy", "记忆有效期（天）",
        "跨会话记忆超过该天数后不再被检索召回，并由后台任务清理。业务知识会过期（旧数据/旧流程），"
        "过期记忆不该再作为回答依据。", "hot",
    ),
    "memory.max_per_user": ConfigItem(
        500, "int", "memory", "policy", "每用户记忆条数上限",
        "每个用户的记忆库最多保留的条目数，超出后自动淘汰最旧的。控制记忆库无限增长带来的检索噪声与存储。", "hot",
    ),
}
