"""配置中心服务: sys_config 表 → 内存缓存, hot 键即时生效。

设计要点:
- 启动时全量载入内存; 读是无锁 dict 查询(原子), 写用 RLock(入库 daemon 线程 + async API 并发)。
- 懒加载: 首次 get_*() 时尝试 load(); 表尚不存在(首次启动 create_all 前)则降级为默认值并标记已加载,
  由 init_db 在 create_all + seed 后显式 load() 补齐, 避免 get_* 在热路径上反复触发 DB 查询。
- hot 键在调用处每次 get_*(); restart 键(model 组, LLM 为启动构建单例)启动时读取, UI 标注重启生效。
"""
import threading
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import select

from core.config_defaults import DEFAULTS
from ingestion.sync_db import SyncSession
from models.config import SysConfig

_cache: Dict[str, str] = {}  # key -> value(字符串存储)
_meta: Dict[str, dict] = {}  # key -> {value_type, group, label, description, apply_mode}
_loaded: bool = False
_lock = threading.RLock()


# ============ 内部: 校验与解析 ============

def _parse(value: str, value_type: str):
    """按 value_type 解析字符串为实际类型, 解析失败返回 None。"""
    try:
        if value_type == "int":
            return int(value)
        if value_type == "float":
            return float(value)
        if value_type == "bool":
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        return value
    except (TypeError, ValueError):
        return None


def _serialize(value) -> str:
    """把配置值序列化为字符串存储。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ============ 载入 ============

def seed_if_empty() -> int:
    """sys_config 表空时按 DEFAULTS 写入种子, 返回写入条数。"""
    with SyncSession() as db:
        count = db.execute(select(SysConfig).limit(1)).scalar_one_or_none()
        if count is not None:
            return 0
        for key, item in DEFAULTS.items():
            db.add(SysConfig(
                key=key,
                value=_serialize(item.value),
                value_type=item.value_type,
                group=item.group,
                label=item.label,
                description=item.description,
                apply_mode=item.apply_mode,
            ))
        db.commit()
        logger.info("配置中心: sys_config 已写入 {} 条默认值", len(DEFAULTS))
        return len(DEFAULTS)


def load() -> None:
    """全量载入内存缓存(幂等, init_db 在 seed 后显式调用)。"""
    global _loaded
    with _lock:
        try:
            seed_if_empty()
        except Exception as exc:
            logger.warning("配置中心: seed 失败(表可能未创建), 忽略: {}", exc)
            _loaded = True  # 防热路径反复尝试
            return
        rows = None
        try:
            with SyncSession() as db:
                rows = db.execute(
                    select(SysConfig).where(SysConfig.is_active.is_(True))
                ).scalars().all()
        except Exception as exc:
            logger.warning("配置中心: 读取失败, 本次以默认值运行: {}", exc)
            _loaded = True
            return
        _cache.clear()
        _meta.clear()
        for r in rows:
            if r.key not in DEFAULTS:
                # 已从配置中心下线的 key(如曾入中心的 model.*/ocr.*)忽略, 不进入缓存
                continue
            _cache[r.key] = r.value
            _meta[r.key] = {
                "value_type": r.value_type,
                "group": r.group,
                "label": r.label,
                "description": r.description or "",
                "apply_mode": r.apply_mode,
            }
        _loaded = True
        logger.info("配置中心: 已加载 {} 条配置(忽略已下线 {} 条)",
                    len(_cache), len(rows) - len(_cache))


def _ensure_loaded() -> None:
    """懒加载: 首次访问时尝试 load(); 失败则标记已加载以默认值运行。"""
    global _loaded
    if _loaded:
        return
    load()


# ============ 读取(hot 调用点) ============

def get_str(key: str, default: str = "") -> str:
    _ensure_loaded()
    val = _cache.get(key)
    if val is None:
        return default
    parsed = _parse(val, _meta.get(key, {}).get("value_type", "str"))
    return str(parsed) if parsed is not None else default


def get_int(key: str, default: int = 0) -> int:
    _ensure_loaded()
    val = _cache.get(key)
    if val is None:
        return default
    parsed = _parse(val, _meta.get(key, {}).get("value_type", "str"))
    return parsed if isinstance(parsed, int) else default


def get_float(key: str, default: float = 0.0) -> float:
    _ensure_loaded()
    val = _cache.get(key)
    if val is None:
        return default
    parsed = _parse(val, _meta.get(key, {}).get("value_type", "str"))
    return parsed if isinstance(parsed, float) else default


def get_bool(key: str, default: bool = False) -> bool:
    _ensure_loaded()
    val = _cache.get(key)
    if val is None:
        return default
    parsed = _parse(val, _meta.get(key, {}).get("value_type", "str"))
    return parsed if isinstance(parsed, bool) else default


def get_group(group: str) -> List[dict]:
    """按组返回配置项列表(供管理 UI), 含默认值与生效模式。"""
    _ensure_loaded()
    items = []
    for key, item in DEFAULTS.items():
        if item.group != group:
            continue
        stored = _cache.get(key)
        items.append({
            "key": key,
            "label": item.label,
            "value": stored if stored is not None else _serialize(item.value),
            "value_type": item.value_type,
            "description": item.description,
            "apply_mode": _meta.get(key, {}).get("apply_mode", item.apply_mode),
            "default": _serialize(item.value),
            "group": group,
            "section": item.section,
        })
    return items


# ============ 写入(管理 API) ============

def update(key: str, value: str, updated_by: Optional[int] = None) -> dict:
    """更新单个配置项: 校验 → 写 DB → 刷新缓存。返回 {key, apply_mode}。"""
    item = DEFAULTS.get(key)
    if item is None:
        raise KeyError(f"未知配置项: {key}")
    serialized = _serialize(value) if item.value_type == "str" else str(value)
    if _parse(serialized, item.value_type) is None:
        raise ValueError(f"{key} 不是合法的 {item.value_type} 值: {value}")
    with _lock:
        with SyncSession() as db:
            row = db.execute(
                select(SysConfig).where(SysConfig.key == key)
            ).scalar_one_or_none()
            if row is None:
                db.add(SysConfig(
                    key=key, value=serialized, value_type=item.value_type,
                    group=item.group, label=item.label, description=item.description,
                    apply_mode=item.apply_mode,
                ))
            else:
                row.value = serialized
                row.updated_by = updated_by
            db.commit()
        _cache[key] = serialized
        if key not in _meta:
            _meta[key] = {
                "value_type": item.value_type, "group": item.group, "label": item.label,
                "description": item.description, "apply_mode": item.apply_mode,
            }
        logger.info("配置中心: {} = {} ({}生效)", key, serialized, item.apply_mode)
        return {"key": key, "apply_mode": item.apply_mode}


def update_batch(items: List[dict], updated_by: Optional[int] = None) -> List[dict]:
    """批量更新, 单条失败不影响已成功的。返回每条结果(含错误)。"""
    results = []
    for it in items:
        key, value = it.get("key"), it.get("value")
        if not key or value is None:
            results.append({"key": key, "error": "缺少 key 或 value"})
            continue
        try:
            results.append(update(key, str(value), updated_by))
        except (KeyError, ValueError) as exc:
            results.append({"key": key, "error": str(exc)})
    return results


def reset_group(group: str) -> int:
    """整组恢复默认值, 返回重置条数。"""
    count = 0
    with _lock:
        for key, item in DEFAULTS.items():
            if item.group != group:
                continue
            serialized = _serialize(item.value)
            with SyncSession() as db:
                row = db.execute(
                    select(SysConfig).where(SysConfig.key == key)
                ).scalar_one_or_none()
                if row is not None:
                    row.value = serialized
                else:
                    db.add(SysConfig(
                        key=key, value=serialized, value_type=item.value_type,
                        group=item.group, label=item.label, description=item.description,
                        apply_mode=item.apply_mode,
                    ))
                db.commit()
            _cache[key] = serialized
            count += 1
        logger.info("配置中心: 组 {} 已重置 {} 条", group, count)
        return count
