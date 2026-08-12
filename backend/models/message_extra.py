"""消息扩展模型: message_images / message_traces。

schema_v2 规范化: 原 messages.metadata(JSON 垃圾桶)拆为两张独立表。
- message_images: 消息关联图片(存引用, 不存 base64)
- message_traces: AI 消息的中间过程追踪(只写不回流, 供审计/调优)
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
)

from db.mysql import Base


class MessageImage(Base):
    """消息图片表: 一条消息可有多张图, 按类型区分。"""

    __tablename__ = "message_images"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(
        BigInteger,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # input=用户输入图 / retrieved=检索命中图 / history=历史图
    image_type = Column(Enum("input", "retrieved", "history"), default="input")
    # 图片引用: /uploads/xx.png 或完整 URL; 刻意不存 base64(避免消息表膨胀)
    image_ref = Column(String(512), nullable=False)
    # 图片描述(可选, 取自 image_analysis 的 caption)
    caption = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)


class MessageTrace(Base):
    """消息追踪表: 与 AI 消息 1:1, 记录本轮中间过程(等效 ragent t_message 的证据列)。"""

    __tablename__ = "message_traces"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(
        BigInteger,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    session_id = Column(String(36), index=True)
    input_text = Column(String(200))
    modality = Column(String(20))  # 输入模态: text / image / text_image
    image_caption = Column(String(200))
    image_relation = Column(String(20))
    rewritten_query = Column(String(300))
    sub_questions = Column(JSON)  # list[str]
    kb_context = Column(JSON)     # list[{filename, category, score}]
    kb_images = Column(JSON)      # list[str] 图片引用
    retrieval_ok = Column(Boolean)
    evaluate_score = Column(Float)
    needs_review = Column(Boolean, default=False)  # 普通用户低分回答已交付, 待管理端审核
    human_answer = Column(String(10))
    duration_ms = Column(Integer)
    # 引用证据(方案B): 回答引用的来源(图片/文本), 供历史回放还原证据区; 只写不回流
    evidence = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
