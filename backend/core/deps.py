"""FastAPI 依赖注入:获取当前登录用户。"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
from loguru import logger

from db.mysql import get_db
from models.user import User
from core.security import verify_token

# OAuth2 密码模式的 token 获取地址
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """根据 JWT token 解析当前用户。

    Args:
        token: Bearer token
        db: 异步数据库 session

    Returns:
        当前 User 对象

    Raises:
        HTTPException(401): token 无效或用户不存在
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = verify_token(token)
        user_id = payload.get("user_id")
        if user_id is None:
            logger.warning("token 中缺少 user_id")
            raise credentials_exception
    except jwt.PyJWTError as exc:
        logger.warning("token 验证失败: {}", exc)
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("token 对应的用户不存在: user_id={}", user_id)
        raise credentials_exception
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """要求当前用户是管理员。"""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足,需要管理员权限",
        )
    return user
