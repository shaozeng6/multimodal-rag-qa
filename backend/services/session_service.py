"""会话 CRUD 业务逻辑。"""
import uuid
from typing import List, Optional

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import Session


async def list_sessions(db: AsyncSession, user_id: int) -> List[Session]:
    """获取指定用户的所有会话列表(按更新时间倒序)。"""
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id)
        .order_by(Session.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_session(
    db: AsyncSession,
    user_id: int,
    title: str = "新会话",
) -> Session:
    """新建会话,使用 UUID 作为主键(同时作为 LangGraph thread_id)。"""
    session = Session(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("新建会话 session_id={} user_id={}", session.id, user_id)
    return session


async def get_session(
    db: AsyncSession,
    session_id: str,
    user_id: int,
) -> Optional[Session]:
    """获取会话详情,只能获取属于该用户的会话。"""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def delete_session(
    db: AsyncSession,
    session_id: str,
    user_id: int,
) -> bool:
    """删除会话,只能删除属于该用户的会话。

    注意:Redis 中的 checkpointer 数据由调用方(api 层)负责清理。
    Returns:
        是否删除成功
    """
    session = await get_session(db, session_id, user_id)
    if session is None:
        return False
    await db.execute(delete(Session).where(Session.id == session_id))
    await db.commit()
    logger.info("删除会话 session_id={} user_id={}", session_id, user_id)
    return True


async def rename_session(
    db: AsyncSession,
    session_id: str,
    user_id: int,
    title: str,
) -> Optional[Session]:
    """重命名会话标题。"""
    session = await get_session(db, session_id, user_id)
    if session is None:
        return None
    session.title = title[:200]  # 限制长度
    await db.commit()
    await db.refresh(session)
    logger.info("重命名会话 session_id={} title={}", session_id, session.title)
    return session


async def auto_title_session(
    db: AsyncSession,
    session_id: str,
    user_id: int,
    first_message: str,
    first_image: str = None,
) -> Optional[str]:
    """如果会话标题仍是默认值"新会话",根据首条消息自动生成标题。

    纯文本: 取前 20 字符
    纯图片: 用多模态 LLM 看图生成简短标题
    图文: 取文本前 20 字符(文本优先)
    """
    session = await get_session(db, session_id, user_id)
    if session is None or session.title != "新会话":
        return None

    if first_message and first_message.strip():
        # 有文本: 取前 20 字符
        title = first_message.strip().replace("\n", " ")[:20]
        if len(first_message.strip()) > 20:
            title += "..."
    elif first_image:
        # 纯图片: 用多模态 LLM 生成简短标题
        title = await _generate_image_title(first_image)
    else:
        return None

    session.title = title or "新会话"
    await db.commit()
    await db.refresh(session)
    logger.info("自动生成会话标题 session_id={} title={}", session_id, session.title)
    return session.title


async def _generate_image_title(image_url: str) -> str:
    """用多模态 LLM 看图生成简短标题(不超过 20 字)。"""
    try:
        from langchain_core.messages import HumanMessage

        from graph.llm_init import multiModal_llm

        message = HumanMessage(content=[
            {"type": "text", "text": "请用不超过20个汉字简短描述这张图片,作为会话标题。只输出标题文字,不要输出其他内容。"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ])
        response = await multiModal_llm.ainvoke([message])
        title = response.content if isinstance(response.content, str) else str(response.content)
        title = title.strip().replace("\n", " ")[:20]
        if len(title) > 20:
            title = title[:20] + "..."
        return title or "图片会话"
    except Exception as e:
        logger.warning("图片标题生成失败, 使用默认标题: {}", e)
        return "图片会话"


async def verify_session_owner(
    db: AsyncSession,
    session_id: str,
    user_id: int,
) -> Optional[Session]:
    """验证会话归属,返回 Session 或 None(不存在或不属于该用户)。"""
    return await get_session(db, session_id, user_id)
