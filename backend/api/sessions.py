"""会话路由:会话列表、新建、详情、删除、历史消息。"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.deps import get_current_user
from db.mysql import get_db
from models.user import User
from services.message_service import (
    list_message_evidence,
    list_message_images,
    list_message_scores,
    list_messages,
)
from services.session_service import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
    rename_session,
)

router = APIRouter(prefix="/sessions", tags=["会话"])


class SessionCreateRequest(BaseModel):
    """新建会话请求体。"""

    title: Optional[str] = "新会话"


class SessionRenameRequest(BaseModel):
    """重命名会话请求体。"""

    title: str


class SessionResponse(BaseModel):
    """会话响应体。"""

    id: str
    user_id: int
    title: str

    model_config = {"from_attributes": True}


class SessionDetailResponse(BaseModel):
    """会话详情响应体。

    注意:messages 暂时返回空列表,历史消息请用 GET /{id}/messages 接口获取。
    """

    id: str
    user_id: int
    title: str
    messages: List[dict] = []


class MessageResponse(BaseModel):
    """消息响应体。"""

    id: int
    role: str
    content: str
    images: List[str] = []
    # 引用证据(方案B, AI 消息): 历史回放还原证据区; 老消息无则 None
    evidence: Optional[List[dict]] = None
    # 评估置信分(0-100, AI 消息): 历史回放还原置信章; 无则 None
    score: Optional[float] = None
    created_at: Optional[datetime] = None


@router.get("", response_model=List[SessionResponse])
async def get_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的所有会话列表。"""
    sessions = await list_sessions(db, current_user.id)
    return [SessionResponse.model_validate(s) for s in sessions]


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_new_session(
    req: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新建会话。"""
    session = await create_session(db, current_user.id, title=req.title)
    return SessionResponse.model_validate(session)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取会话详情。

    注意:messages 暂时返回空,后续从 Redis checkpointer 中恢复。
    """
    session = await get_session(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    return SessionDetailResponse(
        id=session.id,
        user_id=session.user_id,
        title=session.title,
        messages=[],
    )


@router.get("/{session_id}/messages", response_model=List[MessageResponse])
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取会话的历史消息(从 MySQL 读取,按时间正序)。"""
    session = await get_session(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    messages = await list_messages(session_id)
    # schema_v2: 图片从 message_images 读取(存引用, 按消息分组)
    images_by_msg = await list_message_images(session_id)
    # 方案B: 引用证据从 message_traces.evidence 读取(历史回放还原证据区)
    evidence_by_msg = await list_message_evidence(session_id)
    # 置信分从 message_traces.evaluate_score 读取(0-1 → 0-100)
    scores_by_msg = await list_message_scores(session_id)
    return [
        MessageResponse(
            id=m.id,
            role=m.role,
            content=m.content or "",
            images=images_by_msg.get(m.id, []),
            evidence=evidence_by_msg.get(m.id),
            score=scores_by_msg.get(m.id),
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.patch("/{session_id}", response_model=SessionResponse)
async def rename_session_route(
    session_id: str,
    req: SessionRenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重命名会话标题。"""
    session = await rename_session(db, session_id, current_user.id, req.title)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    return SessionResponse.model_validate(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除会话,同时清理 Redis 中的 checkpointer 数据。"""
    deleted = await delete_session(db, session_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )

    # 清理 Redis 中 LangGraph checkpointer 的数据
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        # checkpointer 通常使用 thread_id 作为 key 前缀的一部分
        # 这里删除以 session_id 为前缀的所有 key
        deleted_count = 0
        async for key in redis_client.scan_iter(match=f"*{session_id}*", count=100):
            await redis_client.delete(key)
            deleted_count += 1
        await redis_client.close()
        logger.info("已清理会话 {} 的 Redis checkpointer 数据,共 {} 个 key", session_id, deleted_count)
    except Exception as exc:
        # Redis 清理失败不影响会话删除主流程,仅记录日志
        logger.warning("清理会话 {} 的 Redis 数据失败: {}", session_id, exc)

    return None
