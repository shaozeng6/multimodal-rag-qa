"""人工审核队列(仅管理员): 普通用户低分回答已交付、待管理端复核。

背景: 审批按角色分流后, 普通用户低分回答不再中断会话, 而是直接交付并打 needs_review 标记;
管理端在此队列查看并处理(通过 / 忽略)。管理员自己的会话仍走即时审批中断(原流程)。
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import require_admin
from db.mysql import get_db
from models.message_extra import MessageTrace
from models.session import Message
from models.user import User

router = APIRouter(prefix="/admin/reviews", tags=["审核队列"])


class ReviewItem(BaseModel):
    """待审核项。"""

    message_id: int
    session_id: str
    query: str          # 用户问题(来自 trace.input_text)
    answer: str         # AI 回答
    score: float        # 0-1
    created_at: datetime


class ReviewResolve(BaseModel):
    """处理审核项: approve 通过(记录) / dismiss 忽略。"""

    action: str


@router.get("", response_model=List[ReviewItem])
async def list_reviews(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """待审核列表(按时间倒序)。"""
    result = await db.execute(
        select(MessageTrace, Message)
        .join(Message, Message.id == MessageTrace.message_id)
        .where(MessageTrace.needs_review.is_(True))
        .order_by(MessageTrace.id.desc())
    )
    items = []
    for trace, msg in result.all():
        items.append(ReviewItem(
            message_id=msg.id,
            session_id=trace.session_id or "",
            query=(trace.input_text or "")[:200],
            answer=msg.content or "",
            score=trace.evaluate_score if trace.evaluate_score is not None else 0.0,
            created_at=msg.created_at,
        ))
    return items


@router.post("/{message_id}/resolve")
async def resolve_review(
    message_id: int,
    req: ReviewResolve,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """处理审核项: 清除待审标记, 记录处理动作(approve 通过 / dismiss 忽略)。"""
    if req.action not in ("approve", "dismiss"):
        raise HTTPException(status_code=400, detail="action 仅支持 approve/dismiss")
    trace = await db.scalar(
        select(MessageTrace).where(MessageTrace.message_id == message_id)
    )
    if trace is None or not trace.needs_review:
        raise HTTPException(status_code=404, detail="审核项不存在或已处理")
    trace.needs_review = False
    trace.human_answer = "approved" if req.action == "approve" else "dismissed"
    await db.commit()
    return {"ok": True, "message_id": message_id, "action": req.action}
