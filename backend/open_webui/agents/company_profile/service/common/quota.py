"""每日访问次数限额 — worker 与 monitor 共用的同一份逻辑。

worker 侧（执行限额）：
  - parse_quota_limit      解析 DAILY_VISIT_LIMIT（fail-open）
  - quota_key              生成今日计数键
  - RESERVE_SLOT / reserve_slot / release_slot   原子预占 / 退还
  - write_quota_meta       启动时写配置 meta 键（单一事实源）

monitor 侧（只读观察）：
  - read_quota_meta        读取 worker 写入的配置
  - get_quota_status       汇总 used/limit/remaining/config_error
  - reset_quota            删除今日计数键（手动重置）

worker 是额度配置的唯一解析者；monitor 只读 Redis，不重复解析，避免双份漂移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Tuple
from zoneinfo import ZoneInfo

# 整轮循环复用同一个 key；超限则 DECR 回退，避免名额泄漏。
# EXPIRE 2 天兜底，防止异常崩溃残留死键。
RESERVE_SLOT = """
local used = redis.call('INCR', KEYS[1])
if used > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return 0
end
redis.call('EXPIRE', KEYS[1], 172800)
return 1
"""

RELEASE_SLOT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used > 0 then
  redis.call('DECR', KEYS[1])
  return 1
end
return 0
"""


def parse_quota_limit(raw) -> Tuple[int, bool, str | None]:
    """解析 DAILY_VISIT_LIMIT。

    返回 (limit, unlimited, error)：
      - 空 / None / "0" → (0, True, None)  不限
      - 正整数           → (N, False, None)
      - 负数 / 非整数    → (0, True, "<描述>")  fail-open（非法配置；worker 据此告警并退出）
    """
    if raw is None:
        raw = ""
    text = str(raw).strip()
    if text == "":
        return 0, True, None
    try:
        n = int(text)
    except ValueError:
        return 0, True, f"非法数值（非整数）: {raw!r}"
    if n == 0:
        return 0, True, None
    if n < 0:
        return 0, True, f"非法数值（负数）: {n}"
    return n, False, None


def quota_key(service_name: str, timezone_str: str = "Asia/Shanghai", now=None) -> str:
    """生成今日计数键 quota:used:{service_name}:{YYYY-MM-DD}。"""
    tz = ZoneInfo(timezone_str)
    moment = now if now is not None else datetime.now(tz)
    return f"quota:used:{service_name}:{moment.strftime('%Y-%m-%d')}"


def reserve_slot(redis_client, key: str, limit: int) -> bool:
    """原子预占一个名额。成功 True，已超额 False。"""
    return bool(redis_client.eval(RESERVE_SLOT, 1, key, limit))


def release_slot(redis_client, key: str) -> None:
    """安全退还一个名额（空队列或未取到任务时调用）。"""
    redis_client.eval(RELEASE_SLOT, 1, key)


def write_quota_meta(
    redis_client,
    service_name: str,
    limit: int,
    unlimited: bool,
    config_error: str | None,
    timezone_str: str,
) -> None:
    """将解析后的额度配置写入 Redis（单一事实源，供监控面板读取）。"""
    pipe = redis_client.pipeline()
    pipe.set(f"quota:limit:{service_name}", "unlimited" if unlimited else str(limit))
    pipe.set(f"quota:config_error:{service_name}", config_error or "")
    pipe.set(f"quota:timezone:{service_name}", timezone_str)
    pipe.execute()


def read_quota_meta(redis_client, service_name: str) -> dict:
    """读取 worker 写入的额度配置。worker 未运行时返回 {available: False}。"""
    limit_raw = redis_client.get(f"quota:limit:{service_name}")
    if limit_raw is None:
        return {"available": False}
    config_error = redis_client.get(f"quota:config_error:{service_name}") or ""
    timezone_str = redis_client.get(f"quota:timezone:{service_name}") or "Asia/Shanghai"
    unlimited = limit_raw == "unlimited"
    limit = 0 if unlimited else int(limit_raw)
    return {
        "available": True,
        "limit": limit,
        "unlimited": unlimited,
        "config_error": config_error,
        "timezone": timezone_str,
    }


def get_quota_status(
    redis_client,
    service_name: str,
    fallback_timezone: str = "Asia/Shanghai",
    now=None,
) -> dict[str, Any]:
    meta = read_quota_meta(redis_client, service_name)
    if not meta.get("available"):
        return {"service_name": service_name, "available": False}
    tz = meta["timezone"] or fallback_timezone
    key = quota_key(service_name, tz, now=now)
    used = int(redis_client.get(key) or 0)
    unlimited = meta["unlimited"]
    limit = meta["limit"]
    return {
        "service_name": service_name,
        "available": True,
        "used": used,
        "limit": limit,
        "unlimited": unlimited,
        "remaining": None if unlimited else max(0, limit - used),
        "config_error": meta["config_error"],
        "timezone": tz,
        "date": key.rsplit(":", 1)[-1],
    }


def reset_quota(
    redis_client,
    service_name: str,
    fallback_timezone: str = "Asia/Shanghai",
    now=None,
) -> dict[str, Any]:
    meta = read_quota_meta(redis_client, service_name)
    tz = (meta.get("timezone") or fallback_timezone) if meta.get("available") else fallback_timezone
    key = quota_key(service_name, tz, now=now)
    deleted = redis_client.delete(key)
    return {
        "service_name": service_name,
        "reset": True,
        "deleted": deleted,
        "date": key.rsplit(":", 1)[-1],
    }
