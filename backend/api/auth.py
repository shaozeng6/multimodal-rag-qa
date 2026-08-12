"""认证路由:登录、获取当前用户信息、修改密码。"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_user
from core.security import hash_password, verify_password
from db.mysql import get_db
from models.user import User
from services.auth_service import authenticate_user, create_token_for_user

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """修改密码请求体。"""

    old_password: str
    new_password: str


class UserResponse(BaseModel):
    """用户信息响应体。"""

    id: int
    username: str
    role: str
    # P0: 首登强制改密标记(前端据此拦截到改密页)
    must_change_password: bool = False

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """登录响应体。"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    must_change_password: bool = False


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
        must_change_password=bool(user.must_change_password),
    )


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    req: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改当前用户密码(首登强制改密用): 校验旧密码 → 更新哈希 → 清除改密标记。"""
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="旧密码不正确")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码长度至少 6 位")
    if req.new_password == req.old_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与旧密码相同")

    current_user.password_hash = hash_password(req.new_password)
    current_user.must_change_password = False
    await db.commit()
    return UserResponse.model_validate(current_user)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息。"""
    return UserResponse.model_validate(current_user)
