"""安全工具模块:JWT 生成/验证 + 密码哈希。"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

import jwt
from passlib.context import CryptContext
from loguru import logger

from core.config import settings

# bcrypt 密码哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(
    user_id: int,
    username: str,
    role: str,
    expires_minutes: Optional[int] = None,
) -> str:
    """生成 JWT access token。

    Args:
        user_id: 用户ID
        username: 用户名
        role: 角色 (user/admin)
        expires_minutes: 过期时间(分钟),默认使用配置中的 JWT_EXPIRE_MINUTES

    Returns:
        编码后的 JWT 字符串
    """
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=minutes)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "user_id": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    logger.debug("为用户 {} 生成 token,过期时间: {}", username, expire.isoformat())
    return token


def verify_token(token: str) -> Dict[str, Any]:
    """验证 JWT token 并返回 payload。

    Args:
        token: JWT 字符串

    Returns:
        解码后的 payload 字典

    Raises:
        jwt.PyJWTError: token 无效或过期
    """
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    return payload


def hash_password(password: str) -> str:
    """对明文密码进行哈希。"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配。"""
    return pwd_context.verify(plain_password, hashed_password)
