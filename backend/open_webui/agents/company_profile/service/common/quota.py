"""每日访问次数限额 — worker 与 monitor 共用的同一份逻辑。

worker 侧（执行限额）：
  - parse_quota_limit      解析 DAILY_VISIT_LIMIT（fail-open）
  - quota_key              生成今日计数键
  - RESERVE_SLOT / reserve_slot / release_slot   原子预占 / 退还
  - QuotaWaiter            等待手动重置或自然跨日
  - write_quota_meta       启动时写配置 meta 键（单一事实源）

monitor 侧（观察与手动重置）：
  - read_quota_meta        读取 worker 写入的配置
  - get_quota_status       汇总 used/limit/remaining/config_error
  - reset_quota            删除今日计数键（手动重置）

worker 是额度配置的唯一解析者；monitor 读取 Redis 并通过共享逻辑手动重置。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable, Tuple
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

# 手动重置必须同时删除计数并广播，避免 Worker 继续等待。
QUOTA_RESET_SCRIPT = """
local deleted = redis.call('DEL', KEYS[1])
redis.call('PUBLISH', ARGV[1], 'reset')
return deleted
"""

WAKE_MANUAL_RESET = "manual_reset"
WAKE_DAY_ROLLOVER = "day_rollover"
WAKE_TIMEOUT = "timeout"


def _quota_moment(timezone_str: str, now=None) -> datetime:
    """ 将时间统一转换成指定时区下的时间 """
    tz = ZoneInfo(timezone_str)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


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
    moment = _quota_moment(timezone_str, now)
    return f"quota:used:{service_name}:{moment.strftime('%Y-%m-%d')}"


def quota_reset_channel(service_name: str) -> str:
    """生成手动重置广播频道。"""
    return f"quota:reset:{service_name}"


def seconds_until_next_quota_day(
    timezone_str: str = "Asia/Shanghai",
    now=None,
) -> float:
    """计算距离下一个自然日的秒数。"""
    tz = ZoneInfo(timezone_str)
    moment = _quota_moment(timezone_str, now)
    next_day = datetime.combine(
        moment.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=tz,
    )
    return max(0.0, next_day.timestamp() - moment.timestamp())


class QuotaWaiter:
    """额度等待器：监听手动重置，并在自然跨日时结束等待。"""

    def __init__(
        self,
        redis_client,
        service_name: str,
        timezone_str: str = "Asia/Shanghai",
        *,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        self.redis_client = redis_client
        self.service_name = service_name
        self.timezone_str = timezone_str
        self._now = now_provider or (lambda: datetime.now(ZoneInfo(timezone_str)))
        self._monotonic = monotonic_provider or time.monotonic
        self._pubsub = None
        self._ensure_subscription()

    def wait(self, max_seconds: float) -> str:
        """等待额度恢复，返回手动重置、跨日或超时原因。"""
        self._ensure_subscription()
        initial_key = quota_key(
            self.service_name,
            self.timezone_str,
            now=self._now(),
        )
        deadline = self._monotonic() + max(0.0, max_seconds)

        try:
            while True:
                moment = self._now()
                current_key = quota_key(
                    self.service_name,
                    self.timezone_str,
                    now=moment,
                )
                if current_key != initial_key:
                    return WAKE_DAY_ROLLOVER

                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return WAKE_TIMEOUT

                wait_seconds = min(
                    remaining,
                    seconds_until_next_quota_day(self.timezone_str, now=moment),
                )
                if wait_seconds <= 0:
                    continue

                message = self._pubsub.get_message(timeout=wait_seconds)
                if message and message.get("type") == "message":
                    return WAKE_MANUAL_RESET
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """关闭 Redis Pub/Sub 连接。"""
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            try:
                pubsub.close()
            except Exception:
                pass

    def _ensure_subscription(self) -> None:
        if self._pubsub is not None:
            return
        pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(quota_reset_channel(self.service_name))
        except Exception:
            pubsub.close()
            raise
        self._pubsub = pubsub


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
    """原子重置当天额度并广播，唤醒等待中的 Worker。"""
    meta = read_quota_meta(redis_client, service_name)
    tz = (meta.get("timezone") or fallback_timezone) if meta.get("available") else fallback_timezone
    key = quota_key(service_name, tz, now=now)
    deleted = redis_client.eval(
        QUOTA_RESET_SCRIPT,
        1,
        key,
        quota_reset_channel(service_name),
    )
    return {
        "service_name": service_name,
        "reset": True,
        "deleted": deleted,
        "date": key.rsplit(":", 1)[-1],
    }
