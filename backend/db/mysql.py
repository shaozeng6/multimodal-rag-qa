"""MySQL 异步连接模块,提供 SQLAlchemy 异步 engine / session 以及 Base。"""
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

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


async def _ensure_column(conn, table: str, column: str, ddl: str) -> bool:
    """迁移: 已有表缺指定列时自动补齐。

    create_all 只建新表、不给已有表加列; schema_v2.sql 里的 ALTER 是注释。
    启动时检查 information_schema, 缺列则 ALTER, 幂等可重复执行。
    Returns: 是否本次新增了该列(调用方可据此做一次性数据迁移)。
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
            return True
        logger.info("迁移: {}.{} 列已存在, 跳过", table, column)
        return False
    except Exception as e:
        logger.warning("检查/补齐 {}.{} 列失败(忽略, 若表不存在会由 create_all 兜底): {}",
                       table, column, e)
        return False


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
    await _ensure_column(
        conn, "knowledge_documents", "char_count",
        "ALTER TABLE knowledge_documents "
        "ADD COLUMN char_count INT DEFAULT 0 "
        "COMMENT '文本类 chunk 字符数合计(含图片/表格描述)' AFTER image_count",
    )
    await _ensure_column(
        conn, "knowledge_documents", "file_size",
        "ALTER TABLE knowledge_documents "
        "ADD COLUMN file_size BIGINT DEFAULT 0 "
        "COMMENT '源文件字节数' AFTER char_count",
    )


async def _ensure_ingest_columns(conn) -> None:
    """补齐 ingest_jobs 随版本新增的列(schema_v3 进度可视化)。"""
    await _ensure_column(
        conn, "ingest_jobs", "progress",
        "ALTER TABLE ingest_jobs "
        "ADD COLUMN progress INT DEFAULT 0 COMMENT '0~100 进度百分比' AFTER error",
    )
    await _ensure_column(
        conn, "ingest_jobs", "stage_detail",
        "ALTER TABLE ingest_jobs "
        "ADD COLUMN stage_detail VARCHAR(255) DEFAULT '' "
        "COMMENT '阶段明细(如 向量化 45/120)' AFTER progress",
    )
    await _ensure_column(
        conn, "ingest_jobs", "run_mode",
        "ALTER TABLE ingest_jobs "
        "ADD COLUMN run_mode VARCHAR(10) DEFAULT 'auto' "
        "COMMENT '运行模式: auto 自动全流程 / manual 手动分步' AFTER stage_detail",
    )
    await _ensure_column(
        conn, "ingest_jobs", "phase",
        "ALTER TABLE ingest_jobs "
        "ADD COLUMN phase VARCHAR(10) DEFAULT 'parse' "
        "COMMENT '阶段: parse 解析(OCR/分片) / index 索引(描述/向量化/入库)' AFTER run_mode",
    )


async def _ensure_user_columns(conn) -> None:
    """users 表新增列: must_change_password(首登强制改密)。

    存量安装补齐该列时, 把现有 admin 一并标记强制改密(此前一直用默认 admin123, 需立即更换)。
    标记只在"列刚新增"这一次执行, 之后重启不会反复强制。
    """
    added = await _ensure_column(
        conn, "users", "must_change_password",
        "ALTER TABLE users ADD COLUMN must_change_password TINYINT(1) DEFAULT 0 "
        "COMMENT '首次登录是否强制改密' AFTER role",
    )
    if added:
        await conn.execute(text("UPDATE users SET must_change_password = 1 WHERE username = 'admin'"))
        logger.info("迁移: 存量 admin 已标记强制改密")
    await _ensure_column(
        conn, "users", "is_active",
        "ALTER TABLE users ADD COLUMN is_active TINYINT(1) DEFAULT 1 "
        "COMMENT '是否可用(禁用后无法登录)' AFTER must_change_password",
    )


async def _ensure_ingest_status_enum(conn) -> None:
    """ingest_jobs.status Enum 追加 cancelled(MySQL 无 ADD ENUM VALUE, 需整列 MODIFY)。"""
    result = await conn.execute(text(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ingest_jobs' AND COLUMN_NAME = 'status'"
    ))
    row = result.first()
    if row and "cancelled" in (row[0] or ""):
        return
    await conn.execute(text(
        "ALTER TABLE ingest_jobs MODIFY COLUMN status "
        "ENUM('pending','running','success','error','cancelled') NOT NULL DEFAULT 'pending'"
    ))
    logger.info("迁移: ingest_jobs.status 已追加 cancelled 枚举值")


async def init_db() -> None:
    """初始化数据库:创建所有表并插入初始 admin 账号。

    初始账号: username=admin, password=admin123
    """
    # 延迟导入,避免循环依赖; 全部模型都需在此注册, create_all 才会建表
    from core.security import pwd_context  # noqa: WPS433
    from models.config import SysConfig  # noqa: WPS433,F401
    from models.ingestion import IngestJob, KnowledgeDocument  # noqa: WPS433,F401
    from models.message_extra import MessageImage, MessageTrace  # noqa: WPS433,F401
    from models.session import Message, Session  # noqa: WPS433,F401
    from models.user import User  # noqa: WPS433

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表已创建(若不存在)")
        await _ensure_trace_columns(conn)
        await _ensure_knowledge_columns(conn)
        await _ensure_ingest_columns(conn)
        await _ensure_ingest_status_enum(conn)
        await _ensure_user_columns(conn)

    # 配置中心: sys_config 表空则写默认值种子, 并载入内存缓存(懒加载兜底, 幂等)
    from services import config_service  # noqa: WPS433
    config_service.load()
    logger.info("配置中心初始化完成")

    # 插入初始 admin 账号
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if admin is None:
            admin_user = User(
                username="admin",
                password_hash=pwd_context.hash("admin123"),
                role="admin",
                must_change_password=True,  # P0: 首登强制改密, 避免默认凭据长期生效
            )
            session.add(admin_user)
            await session.commit()
            logger.info("初始 admin 账号已创建 (username=admin, password=admin123)")
        else:
            logger.info("admin 账号已存在,跳过创建")
