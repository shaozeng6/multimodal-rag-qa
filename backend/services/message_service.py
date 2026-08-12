"""消息持久化业务逻辑。

schema_v2 规范化: 一条消息落三张表。
- messages          : 文本内容(role/content)
- message_images    : 关联图片(存引用, 不存 base64)
- message_traces    : AI 消息的中间过程追踪(只写不回流)

使用独立 DB session(非请求依赖), 适合在流式响应中调用,
不受 FastAPI 请求生命周期影响。
"""
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import select

from db.mysql import async_session_maker
from models.message_extra import MessageImage, MessageTrace
from models.session import Message
from services.image_store import resolve_image_url

# trace dict(_build_trace 返回)与 MessageTrace 列的映射(排除 message_id 等由函数注入的键)
_TRACE_COLUMNS = {
    "input_text", "modality", "image_caption", "image_relation", "rewritten_query",
    "sub_questions", "kb_context", "kb_images", "retrieval_ok",
    "evaluate_score", "human_answer", "duration_ms", "evidence",
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


async def list_message_scores(session_id: str) -> Dict[int, float]:
    """按消息分组返回评估分(message_id -> 0-100 显示分)。

    历史消息的置信章数据来自 message_traces.evaluate_score(0~1), 前端以 0-100 展示,
    这里统一换算。无评分的消息(如审批通过前)不返回。
    """
    async with async_session_maker() as db:
        result = await db.execute(
            select(MessageTrace.evaluate_score, Message.id)
            .join(Message, Message.id == MessageTrace.message_id)
            .where(Message.session_id == session_id)
        )
        scores: Dict[int, float] = {}
        for evaluate_score, message_id in result.all():
            if evaluate_score is not None:
                scores[message_id] = round(float(evaluate_score) * 100, 1)
        return scores


async def list_message_evidence(session_id: str) -> Dict[int, list]:
    """按消息分组返回引用证据(message_id -> evidence 列表)。

    供历史回放还原证据区: 入库的 evidence 里图片 image_path 是本地路径,
    统一经 resolve_image_url 映射为前端可加载 URL; 文本来源原样返回。
    """
    async with async_session_maker() as db:
        result = await db.execute(
            select(MessageTrace.evidence, Message.id)
            .join(Message, Message.id == MessageTrace.message_id)
            .where(Message.session_id == session_id)
        )
        grouped: Dict[int, list] = {}
        for evidence, message_id in result.all():
            if not evidence:
                continue
            items = []
            for e in evidence:
                item = dict(e)
                if item.get("type") != "text" and item.get("image_path"):
                    url = resolve_image_url(item["image_path"])
                    if not url:
                        continue
                    item["url"] = url
                    item.pop("image_path", None)
                items.append(item)
            if items:
                grouped[message_id] = items
        return grouped
