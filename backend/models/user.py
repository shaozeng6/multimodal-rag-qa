"""User SQLAlchemy 模型。"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String

from db.mysql import Base


class User(Base):
    """用户表模型。"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(Enum("user", "admin"), default="user")
    # P0: 首登强制改密(种子 admin 与存量 admin 迁移后为 True, 改密成功后清除)
    must_change_password = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
