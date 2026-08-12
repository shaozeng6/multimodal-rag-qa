"""认证业务逻辑:登录验证、用户查询。"""
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token, verify_password
from models.user import User


async def authenticate_user(
    db: AsyncSession,
    username: str,
    password: str,
) -> Optional[User]:
    """验证用户名密码。

    Args:
        db: 异步数据库 session
        username: 用户名
        password: 明文密码

    Returns:
        匹配的 User 对象,失败返回 None
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        logger.info("登录失败:用户不存在 username={}", username)
        return None
    if not verify_password(password, user.password_hash):
        logger.info("登录失败:密码错误 username={}", username)
        return None
    logger.info("登录成功 username={}", username)
    return user


def create_token_for_user(user: User) -> str:
    """为指定用户生成 JWT token。"""
    return create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
    )
