"""Session 与 Message SQLAlchemy 模型。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)

from db.mysql import Base


class Session(Base):
    """会话表模型。

    id 为 UUID 字符串,同时作为 LangGraph 的 thread_id。
    """

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(200), default="新会话")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Message(Base):
    """消息表模型。"""

    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(Enum("human", "ai", "tool"), nullable=False)
    content = Column(Text)
    # 规范化(schema_v2): 原 metadata(JSON) 拆为 message_images(图片引用)
    # 与 message_traces(中间过程) 两张表, 见 models/message_extra.py
    created_at = Column(DateTime, default=datetime.now)
