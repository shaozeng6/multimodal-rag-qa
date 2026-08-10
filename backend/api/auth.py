"""认证路由:登录、获取当前用户信息。"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.mysql import get_db
from models.user import User
from core.deps import get_current_user
from services.auth_service import authenticate_user, create_token_for_user

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str
    password: str


class UserResponse(BaseModel):
    """用户信息响应体。"""

    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """登录响应体。"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录,验证后返回 JWT token 和用户信息。"""
    user = await authenticate_user(db, req.username, req.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_token_for_user(user)
    return LoginResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息。"""
    return UserResponse.model_validate(current_user)
