"""系统配置表(schema_v3): 运行参数可视化配置中心。

企业级诉求: RAG 行为参数(召回 topK / 分片字符数 / 评估阈值 / 模型温度等)
不再写死在代码常量或 .env, 而是落 DB 供管理员在「系统设置」页可视化配置。
hot 键保存即生效; restart 键(模型类, LLM 为启动构建单例)需重启。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, String, Text

from db.mysql import Base


class SysConfig(Base):
    """系统配置项: 每个 key 一行, value 按 value_type 解析。"""

    __tablename__ = "sys_config"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)  # 点分键, 如 retrieval.context_topk
    value = Column(Text, nullable=False, default="")  # 字符串存储, 按 value_type 解析
    value_type = Column(Enum("str", "int", "float", "bool"), default="str")
    group = Column(String(50), nullable=False, index=True)  # ingestion/retrieval/evaluation/model/context/rag
    label = Column(String(100), nullable=False)  # 中文名
    description = Column(Text, default="")  # 说明 + 默认值提示
    apply_mode = Column(Enum("hot", "restart"), default="hot")  # hot=即时生效, restart=重启生效
    is_active = Column(Boolean, default=True)
    updated_by = Column(BigInteger, nullable=True)  # 最后修改人(admin user id)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
