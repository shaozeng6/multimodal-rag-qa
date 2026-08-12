"""LangGraph 工作流状态定义。

采用普通 TypedDict(而非 MessagesState)承载业务字段;对话历史以 LangGraph 标准
BaseMessage 列表存放在 messages 通道(add_messages reducer 自动追加), 不混入
工具调用消息 / ToolMessage 污染长期历史。
"""
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


class MultiModalRAGState(TypedDict, total=False):
    """状态数据的结构类。

    字段分组:
    - 当前输入: 每轮由 chat.py / process_input 覆盖
    - 上下文管理: messages(标准 BaseMessage 历史) + summary(滚动摘要) + summary_anchor(水位线)
    - 改写: rewritten_query / sub_questions
    - 检索: kb_context(记忆命中已并入) / kb_images / retrieval_ok
    - 生成与评估: answer / evaluate_score / human_answer / human_reason
    """

    # ---- 当前输入 ----
    # 模态(三值 text/image/text_image)由 process_input 用 input_modality 派生并写入 state,
    # 是本轮状态的组成部分, 供 trace/审计(以及未来路由/评估)读取。
    # (旧的二值 input_type 字段已废弃, 见 PROJECT_REVIEW 模态重构记录)
    input_text: Optional[str]
    input_image: Optional[str]  # base64 data URI 或图片 URL
    modality: str               # 输入模态: text / image / text_image
    user: str                   # 用户名(展示/日志用)
    user_id: Optional[int]      # 用户数字 id(记忆隔离按 id, 改名不影响归属)
    role: str                   # user/admin: 低分审批按角色分流(管理员打断, 普通用户直接交付)
    session_id: str
    start_ts: float             # 本轮开始时间(monotonic), 供 trace 计算耗时

    # ---- 图片理解(P1) ----
    image_caption: str          # 图→描述(caption), 供纯图/图文走文本检索
    image_relation: str         # "related"/"unrelated"/""(纯图或分析失败)

    # ---- 上下文管理 ----
    # 标准 BaseMessage 列表: HumanMessage(content=[{text}, {image_url}]) / AIMessage(纯文本)。
    # add_messages reducer 追加, 生成器直接切窗口传给多模态模型; 历史图随 image_url 块天然携带。
    messages: Annotated[list, add_messages]
    summary: str                # 滚动摘要(更早轮次的语义压缩)
    summary_anchor: int         # 水位线: 上次摘要已覆盖到的 messages 条数

    # ---- 改写 ----
    rewritten_query: str        # 指代消解/补全后的独立问题(纯图时为 caption)
    sub_questions: list         # 复合问题拆分出的子问题

    # ---- 检索 ----
    # kb_context 为统一融合结果(文本/图片/记忆命中), 每项带 category(text/image/memory)
    kb_context: list            # 检索命中: [{"text","category","image_path","filename","score"},...]
    kb_images: list             # 检索到的图片 image_path 列表
    retrieval_ok: bool          # 是否需要检索且检索到了结果

    # ---- 生成与评估 ----
    answer: str                 # 最终回答(评估/持久化直接读它, 不再解析 messages)
    evidence: list              # 方案B: 回答中被引用的 doc 证据(图片+文本来源), 供前端证据区
    evaluate_score: Optional[float]  # LLM Judge 评分 0~1, None=评估失败(区别于低分)
    needs_review: bool          # 普通用户低分回答已交付但需管理端审核(路由/持久化判定)
    human_answer: str           # approve / reject
    human_reason: str           # 人工审批备注/驳回原因
