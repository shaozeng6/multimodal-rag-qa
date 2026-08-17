"""FastAPI 应用入口:多模态 RAG 知识库问答系统企业版后端。"""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.config import router as config_router
from api.files import router as files_router
from api.knowledge import router as knowledge_router
from api.reviews import router as reviews_router
from api.sessions import router as sessions_router
from api.users import router as users_router
from core.config import settings
from db.mysql import init_db
from graph.milvus_writer import purge_expired_memories
from graph.workflow_service import graph, init_checkpointer

# 记忆后台清理间隔(秒, 默认每日)
_MEMORY_CLEANUP_INTERVAL = 86400


async def _memory_cleanup_loop(interval_seconds: int = _MEMORY_CLEANUP_INTERVAL) -> None:
    """后台每日清理过期记忆(内存任务, 随进程生命周期; 失败下轮重试)。"""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(purge_expired_memories)
        except Exception as exc:
            logger.warning("后台记忆清理失败(下轮重试): {}", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:初始化数据库 + 异步初始化 Redis checkpointer + 记忆维护。"""
    logger.info("应用启动,开始初始化数据库...")
    try:
        await init_db()
        logger.info("数据库初始化完成")
    except Exception as exc:
        logger.error("数据库初始化失败: {}", exc)
    # AsyncRedisSaver 需要 asetup() 建索引; InMemorySaver 无操作
    await init_checkpointer(graph)
    # 记忆维护: 启动时清一次过期记忆 + 后台每日清理(失败不影响主流程)
    try:
        await asyncio.to_thread(purge_expired_memories)
    except Exception as exc:
        logger.warning("启动时记忆清理失败(忽略): {}", exc)
    cleanup_task = asyncio.create_task(_memory_cleanup_loop())
    app.state.memory_cleanup_task = cleanup_task
    yield
    cleanup_task.cancel()
    logger.info("应用关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="多模态RAG知识库问答系统",
    description="多模态 RAG 知识库问答系统 - 企业版后端",
    version="1.0.0",
    lifespan=lifespan,
)

# 配置 CORS(开发模式允许所有来源)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有 API 路由(统一前缀 /api)
app.include_router(auth_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")

# 静态文件: /uploads → UPLOAD_IMAGES_DIR(消息图片引用可直接加载)
os.makedirs(settings.UPLOAD_IMAGES_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_IMAGES_DIR), name="uploads")


@app.get("/health", tags=["系统"])
async def health_check():
    """健康检查接口。"""
    return {"status": "ok", "service": "multimodal-rag-enterprise-backend"}
