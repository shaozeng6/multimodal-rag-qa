"""用户管理路由(仅管理员): 列表 / 创建 / 改角色与启禁用 / 重置密码 / 删除。

自我保护守卫(防把自己锁死):
- 不能修改自己的角色、禁用或删除自己
- 不能禁用/删除/降级最后一个管理员
新创建/重置密码的用户 must_change_password=True, 首登强制改密。
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import require_admin
from core.security import hash_password
from db.mysql import get_db
from models.user import User

router = APIRouter(prefix="/admin/users", tags=["用户管理"])


class UserCreate(BaseModel):
    """创建用户请求体。"""

    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    """更新用户请求体(改角色 / 启禁用)。"""

    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResetPassword(BaseModel):
    """重置密码请求体。"""

    new_password: str


class UserResponse(BaseModel):
    """用户信息响应体。"""

    id: int
    username: str
    role: str
    is_active: bool = True
    must_change_password: bool = False
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


async def _active_admin_count(db: AsyncSession, exclude_id: Optional[int] = None) -> int:
    """当前启用的管理员数量(可选排除某个 id)。"""
    q = select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True)
    )
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return await db.scalar(q) or 0


@router.get("", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """用户列表(按 id 正序)。"""
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    req: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """创建用户: 新用户首登强制改密。"""
    if len(req.username.strip()) < 2:
        raise HTTPException(status_code=400, detail="用户名至少 2 个字符")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")
    if req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="角色仅支持 user/admin")

    exists = await db.scalar(select(User).where(User.username == req.username.strip()))
    if exists:
        raise HTTPException(status_code=400, detail=f"用户名 {req.username} 已存在")

    user = User(
        username=req.username.strip(),
        password_hash=hash_password(req.password),
        role=req.role,
        is_active=True,
        must_change_password=True,  # 新用户首登强制改密
    )
    db.add(user)
    await db.commit()
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    req: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """修改角色 / 启禁用(带自我保护守卫)。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current_user.id and (req.role is not None or req.is_active is False):
        raise HTTPException(status_code=400, detail="不能修改自己的角色或禁用自己")

    if req.role is not None:
        if req.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="角色仅支持 user/admin")
        if user.role == "admin" and req.role != "admin":
            if await _active_admin_count(db, exclude_id=user.id) < 1:
                raise HTTPException(status_code=400, detail="至少保留一个管理员")
        user.role = req.role
    if req.is_active is not None:
        if req.is_active is False and user.role == "admin":
            if await _active_admin_count(db, exclude_id=user.id) < 1:
                raise HTTPException(status_code=400, detail="至少保留一个管理员")
        user.is_active = req.is_active
    await db.commit()
    return user


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_password(
    user_id: int,
    req: UserResetPassword,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """管理员重置密码: 重置后该用户首登强制改密。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")
    user.password_hash = hash_password(req.new_password)
    user.must_change_password = True
    await db.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除用户(带自我保护守卫)。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if user.role == "admin":
        if await _active_admin_count(db, exclude_id=user.id) < 1:
            raise HTTPException(status_code=400, detail="至少保留一个管理员")
    await db.delete(user)
    await db.commit()
