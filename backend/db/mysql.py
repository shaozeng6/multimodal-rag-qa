"""MySQL 异步连接模块,提供 SQLAlchemy 异步 engine / session 以及 Base。"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy import select, text
from loguru import logger

from core.config import settings

# 异步 engine
engine = create_async_engine(
    settings.MYSQL_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# 异步 session 工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# ORM 基类,所有模型继承此类
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖:获取异步数据库 session,请求结束自动关闭。"""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _ensure_trace_modality_column(conn) -> None:
    """迁移: 已有 message_traces 表缺 modality 列时自动补齐。

    create_all 只建新表、不给已有表加列; schema_v2.sql 里的 ALTER 是注释。
    启动时检查 information_schema, 缺列则 ALTER, 幂等可重复执行。
    """
    try:
        result = await conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message_traces' "
                "AND COLUMN_NAME = 'modality'"
            )
        )
        if result.first() is None:
            await conn.execute(
                text(
                    "ALTER TABLE message_traces "
                    "ADD COLUMN modality VARCHAR(20) NULL "
                    "COMMENT '输入模态 text/image/text_image' AFTER input_text"
                )
            )
            logger.info("迁移: message_traces 已补齐 modality 列")
        else:
            logger.info("迁移: message_traces.modality 列已存在, 跳过")
    except Exception as e:
        logger.warning("检查/补齐 modality 列失败(忽略, 若表不存在会由 create_all 兜底): {}", e)


async def init_db() -> None:
    """初始化数据库:创建所有表并插入初始 admin 账号。

    初始账号: username=admin, password=admin123
    """
    # 延迟导入,避免循环依赖; 全部模型都需在此注册, create_all 才会建表
    from models.user import User  # noqa: WPS433
    from models.session import Session, Message  # noqa: WPS433,F401
    from models.message_extra import MessageImage, MessageTrace  # noqa: WPS433,F401
    from models.ingestion import IngestJob, KnowledgeDocument  # noqa: WPS433,F401
    from core.security import pwd_context  # noqa: WPS433

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表已创建(若不存在)")
        await _ensure_trace_modality_column(conn)

    # 插入初始 admin 账号
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if admin is None:
            admin_user = User(
                username="admin",
                password_hash=pwd_context.hash("admin123"),
                role="admin",
            )
            session.add(admin_user)
            await session.commit()
            logger.info("初始 admin 账号已创建 (username=admin, password=admin123)")
        else:
            logger.info("admin 账号已存在,跳过创建")
