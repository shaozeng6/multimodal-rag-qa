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


async def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """迁移: 已有表缺指定列时自动补齐。

    create_all 只建新表、不给已有表加列; schema_v2.sql 里的 ALTER 是注释。
    启动时检查 information_schema, 缺列则 ALTER, 幂等可重复执行。
    """
    try:
        result = await conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl "
                "AND COLUMN_NAME = :col"
            ),
            {"tbl": table, "col": column},
        )
        if result.first() is None:
            await conn.execute(text(ddl))
            logger.info("迁移: {} 已补齐 {} 列", table, column)
        else:
            logger.info("迁移: {}.{} 列已存在, 跳过", table, column)
    except Exception as e:
        logger.warning("检查/补齐 {}.{} 列失败(忽略, 若表不存在会由 create_all 兜底): {}",
                       table, column, e)


async def _ensure_trace_columns(conn) -> None:
    """补齐 message_traces 随版本新增的列。"""
    await _ensure_column(
        conn, "message_traces", "modality",
        "ALTER TABLE message_traces "
        "ADD COLUMN modality VARCHAR(20) NULL "
        "COMMENT '输入模态 text/image/text_image' AFTER input_text",
    )
    await _ensure_column(
        conn, "message_traces", "evidence",
        "ALTER TABLE message_traces "
        "ADD COLUMN evidence JSON NULL "
        "COMMENT 'AI回答的引用证据(图片/文本来源), 供历史回放还原证据区' AFTER duration_ms",
    )


async def _ensure_knowledge_columns(conn) -> None:
    """补齐 knowledge_documents 随版本新增的列。"""
    await _ensure_column(
        conn, "knowledge_documents", "milvus_ids",
        "ALTER TABLE knowledge_documents "
        "ADD COLUMN milvus_ids JSON NULL "
        "COMMENT 'Milvus t_doc 主键列表, 按文档精确删除用' AFTER image_count",
    )


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
        await _ensure_trace_columns(conn)
        await _ensure_knowledge_columns(conn)

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
