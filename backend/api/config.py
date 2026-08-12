"""配置中心路由(仅管理员): 运行参数可视化配置。

GET  /config                分组返回全部配置项(含默认值与生效模式)
PUT  /config                批量更新, body=[{"key","value"}, ...]
POST /config/{group}/reset  整组恢复默认值
"""
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config_defaults import DEFAULTS
from core.deps import require_admin
from models.user import User
from services import config_service

router = APIRouter(prefix="/config", tags=["系统配置"])

# 稳定的分组展示顺序(模型等基础设施配置归属 .env, 不进配置中心)
_GROUP_ORDER = ["ingestion", "retrieval", "evaluation", "context", "rag"]
_GROUP_LABELS = {
    "ingestion": "入库",
    "retrieval": "检索",
    "evaluation": "评估",
    "context": "上下文",
    "rag": "图片上限",
}


class ConfigUpdate(BaseModel):
    """单条配置更新请求。"""

    key: str
    value: str


@router.get("")
async def get_config(current_user: User = Depends(require_admin)) -> Dict:
    """分组返回全部配置项(含默认值与生效模式)。"""
    groups = {}
    for group in _GROUP_ORDER:
        if any(item.group == group for item in DEFAULTS.values()):
            groups[group] = {
                "label": _GROUP_LABELS.get(group, group),
                "items": config_service.get_group(group),
            }
    return {"groups": groups}


@router.put("")
async def update_config(
    items: List[ConfigUpdate],
    current_user: User = Depends(require_admin),
) -> Dict:
    """批量更新配置项, 逐条返回结果(含校验错误)。"""
    results = config_service.update_batch(
        [{"key": it.key, "value": it.value} for it in items],
        updated_by=current_user.id,
    )
    errors = [r for r in results if r.get("error")]
    restart_required = [
        r["key"] for r in results if not r.get("error") and r.get("apply_mode") == "restart"
    ]
    resp: Dict = {
        "updated": len(results) - len(errors),
        "errors": errors,
        "restart_required": restart_required,
    }
    if errors:
        resp["message"] = "部分配置保存失败: " + "; ".join(
            f"{e['key']}: {e['error']}" for e in errors
        )
    elif restart_required:
        resp["message"] = "已保存。部分配置需重启后端生效: " + ", ".join(restart_required)
    else:
        resp["message"] = "已保存并即时生效"
    return resp


@router.post("/{group}/reset")
async def reset_config(
    group: str,
    current_user: User = Depends(require_admin),
) -> Dict:
    """整组恢复默认值。"""
    if group not in _GROUP_ORDER:
        raise HTTPException(status_code=404, detail=f"未知配置分组: {group}")
    count = config_service.reset_group(group)
    return {"reset": count, "message": f"已恢复 {group} 组 {count} 条默认值"}
