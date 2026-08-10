"""消息持久化业务逻辑。

schema_v2 规范化: 一条消息落三张表。
- messages          : 文本内容(role/content)
- message_images    : 关联图片(存引用, 不存 base64)
- message_traces    : AI 消息的中间过程追踪(只写不回流)

使用独立 DB session(非请求依赖), 适合在流式响应中调用,
不受 FastAPI 请求生命周期影响。
"""
from typing import Dict, List, Optional

from sqlalchemy import select
from loguru import logger

from models.session import Message
from models.message_extra import MessageImage, MessageTrace
from db.mysql import async_session_maker
from services.image_store import resolve_image_url

# trace dict(_build_trace 返回)与 MessageTrace 列的映射(排除 message_id 等由函数注入的键)
_TRACE_COLUMNS = {
    "input_text", "modality", "image_caption", "image_relation", "rewritten_query",
    "sub_questions", "kb_context", "kb_images", "retrieval_ok",
    "evaluate_score", "human_answer", "duration_ms",
}


async def save_message(
    session_id: str,
    role: str,
    content: str,
    images: Optional[List[str]] = None,
    image_type: str = "input",
    trace: Optional[dict] = None,
) -> None:
    """保存一条消息(含图片引用与中间过程追踪)。

    Args:
        session_id: 会话 ID(同时是 LangGraph thread_id)
        role: 消息角色 human / ai / tool
        content: 文本内容
        images: 关联图片引用列表(URL 或 /uploads 路径, 不传 base64)
        image_type: 图片类型 input/retrieved/history
        trace: AI 消息的中间过程 dict(_build_trace 产物), 落 message_traces
    """
    try:
        async with async_session_maker() as db:
            msg = Message(session_id=session_id, role=role, content=content)
            db.add(msg)
            await db.flush()  # 拿到 msg.id 供子表外键使用

            for ref in images or []:
                if ref:
                    db.add(MessageImage(message_id=msg.id, image_type=image_type, image_ref=ref))

            if trace:
                fields = {k: v for k, v in trace.items() if k in _TRACE_COLUMNS}
                if fields:
                    db.add(MessageTrace(message_id=msg.id, session_id=session_id, **fields))

            await db.commit()
    except Exception as e:
        logger.exception("保存消息失败 session_id={} role={}: {}", session_id, role, e)


async def list_messages(session_id: str) -> List[Message]:
    """获取指定会话的所有消息(按 id 正序,即插入时间顺序)。"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.id.asc())
        )
        return list(result.scalars().all())


async def list_message_images(session_id: str) -> Dict[int, List[str]]:
    """按消息分组返回图片引用(message_id -> [可加载 URL])。

    供历史消息回显使用: 入库引用可能是本地路径, 统一经 resolve_image_url
    映射为前端可加载的 URL(/api/files?path=... 或 /uploads/...)再返回。
    """
    async with async_session_maker() as db:
        result = await db.execute(
            select(MessageImage.image_ref, Message.id)
            .join(Message, Message.id == MessageImage.message_id)
            .where(Message.session_id == session_id)
            .order_by(MessageImage.id.asc())
        )
        grouped: Dict[int, List[str]] = {}
        for image_ref, message_id in result.all():
            url = resolve_image_url(image_ref)
            if url:
                grouped.setdefault(message_id, []).append(url)
        return grouped
