"""入库侧同步数据库访问(pymysql 引擎)。

为何需要: 异步引擎(aiomysql)的连接绑定事件循环, 无法在 daemon 工作线程中使用;
入库管道运行在后台线程, 故单独建一个同步引擎供 ingestion 包使用。
连接是惰性建立的, 数据库未启动时导入不报错。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings

sync_engine = create_engine(
    settings.MYSQL_URL.replace("+aiomysql", "+pymysql"),
    pool_pre_ping=True,
    pool_recycle=3600,
)
SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)
