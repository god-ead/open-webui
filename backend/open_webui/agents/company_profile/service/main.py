""" 业务 Service Worker — 通用任务消费入口，从 Redis 中消费任务 """

import os
import json
import time
import logging
import signal
import sys
import threading

import redis

from app.db import db
from handlers import HandlerRegistry, build_handler_registry
from company_profile.application.errors import (
    CompanyNotFoundError,
    CompanyProfileConfigurationError,
    LLMServiceError,
)
from common.quota import (
    WAKE_TIMEOUT,
    QuotaWaiter,
    parse_quota_limit,
    quota_key,
    reserve_slot,
    write_quota_meta,
)

logger = logging.getLogger("company_profile.worker")

# ── 环境变量 ──────────────────────────────────────────────
SERVICE_ID = os.getenv("SERVICE_ID", "service")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
TASK_QUEUE = os.getenv("TASK_QUEUE", "task_queue")
RESULT_QUEUE = os.getenv("RESULT_QUEUE", "result_queue")
SERVICE_NAME = os.getenv("SERVICE_NAME", "default_service")
DAILY_VISIT_LIMIT = os.getenv("DAILY_VISIT_LIMIT", "0")
DAILY_VISIT_LIMIT_SLEEP_SECONDS = int(os.getenv("DAILY_VISIT_LIMIT_SLEEP_SECONDS", "300"))
QUOTA_LIMIT_STAGE1_BASE_SECONDS = 10
QUOTA_LIMIT_STAGE2_LOG_SECONDS = 60 * 60
# 固定时区 Asia/Shanghai，不开放配置（误配会致 fail-closed）
QUOTA_TIMEZONE = "Asia/Shanghai"

# ── 全局状态 ──────────────────────────────────────────────
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
running = True


def handle_signal(signum, frame):
    global running
    logger.info("收到信号 %s，准备关闭...", signum)
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ── 心跳 ──────────────────────────────────────────────────
def heartbeat():
    """向 Redis 上报存活状态（每 5s 调用一次）"""
    try:
        redis_client.hset(
            f"service:{SERVICE_ID}",
            mapping={
                "last_heartbeat": str(time.time()),
                "status": "active",
            },
        )
        redis_client.expire(f"service:{SERVICE_ID}", 300)
        redis_client.sadd("active_workers", SERVICE_ID)
    except Exception as e:
        logger.warning("心跳失败: %s", e)


def heartbeat_loop():
    """独立线程：每 5 秒发一次心跳，不受任务处理阻塞"""
    while running:
        heartbeat()
        time.sleep(5)


# ── 注册 / 注销 ──────────────────────────────────────────
def register_service():
    redis_client.hset(
        f"service:{SERVICE_ID}",
        mapping={
            "id": SERVICE_ID,
            "type": "worker",
            "status": "active",
            "load": "0",
        },
    )
    redis_client.sadd("active_workers", SERVICE_ID)
    logger.info("服务已注册")


def unregister_service():
    redis_client.srem("active_workers", SERVICE_ID)
    redis_client.delete(f"service:{SERVICE_ID}")


# ── 任务超时 ──────────────────────────────────────────────
class TaskTimeoutError(Exception):
    """任务处理超时异常"""
    pass


def _timeout_handler(signum, frame):
    raise TaskTimeoutError("任务处理超过 20 分钟")


def build_failure_output(error_msg: str, code: int = 3, version: str = "") -> dict:
    """构造统一失败响应。

    Args:
        error_msg: 错误描述信息
        code: 业务状态码，默认 3（企业画像生成失败）。
              1 = 大模型调用异常，2 = 调用失败，3 = 企业画像生成失败
        version: 接口版本号
    """
    _CODE_LABELS = {
        1: "大模型调用异常",
        2: "调用失败",
        3: "企业画像生成失败",
    }
    label = _CODE_LABELS.get(code, "企业画像生成失败")
    return {
        "code": code,
        "message": f"{label}: {error_msg}",
        "timestamp": time.strftime("%Y%m%d%H%M%S", time.localtime()),
        "data": {
            "profile": "",
            "version": version,
        },
    }


def build_result_notification(
    task_id: str,
    status: str,
    worker_id: str,
    task_data: dict,
    result: dict | None = None,
) -> dict:
    """构造结果通知；业务只提供回调明文，发送协议由 API Gateway 处理。"""
    message = {
        "task_id": task_id,
        "status": status,
        "worker": worker_id,
        "callback": task_data.get("callback"),
    }
    # 未完成任务（失败），不向回调接口发送信息
    if status != "completed":
        message["callback"] = None
        return message

    pdfurl = ((result or {}).get("data") or {}).get("profile", "")
    if not message["callback"] or not pdfurl:
        message["callback"] = None
        return message
    message["callback_payload"] = {"task_id": task_id, "pdfurl": pdfurl}
    return message


def pop_limited_task(redis_client, task_queue: str, service_name: str, timezone: str, limit: int):
    """先取任务再扣额度；额度不足时放回队列，避免空队列预占污染监控。"""
    result = redis_client.brpop(task_queue, timeout=1)
    if not result:
        return None, False

    _, task_json = result
    key = quota_key(service_name, timezone)
    if not reserve_slot(redis_client, key, limit):
        redis_client.rpush(task_queue, task_json)
        return None, True

    return json.loads(task_json), False


def quota_limit_sleep_plan(attempts: int) -> tuple[int, str]:
    """额度不足时的两阶段等待策略：先指数退避，再固定间隔检查。"""
    stage1_sleep = QUOTA_LIMIT_STAGE1_BASE_SECONDS * (2 ** min(attempts, 16))
    if stage1_sleep < DAILY_VISIT_LIMIT_SLEEP_SECONDS:
        return stage1_sleep, "stage1"
    return DAILY_VISIT_LIMIT_SLEEP_SECONDS, "stage2"


def should_log_quota_sleep(redis_client, service_name: str, stage: str) -> bool:
    """只对二阶段额度等待提示做 Redis 抢锁，其他日志不受影响。"""
    if stage != "stage2":
        return True

    try:
        key = f"quota:limit_sleep_log:{service_name}:stage2"
        return bool(redis_client.set(key, "1", nx=True, ex=QUOTA_LIMIT_STAGE2_LOG_SECONDS))
    except Exception:
        return False


# ── 任务处理 ──────────────────────────────────────────────
def process_job(task_data: dict, handler_registry: HandlerRegistry):
    """
    处理单个任务的核心逻辑。

    1. 检查任务状态（跳过已完成/失败/取消的任务）
    2. 标记 processing → 执行 handler → 标记 completed/failed
    3. 推送到 RESULT_QUEUE，由 API Gateway 发送回调并通知状态
    """
    task_id = task_data["task_id"]
    start_time = time.time()

    # 设置 20 分钟超时
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(20 * 60)

    try:
        # 检查任务状态，避免重复执行
        with db.conn.cursor() as cur:
            cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if row and row[0] in ("cancelled", "completed", "failed"):
                logger.info("任务 %s 状态为 %s，跳过", task_id, row[0])
                return

        # 标记处理中
        db.mark_processing(task_id, SERVICE_ID)
        logger.info("开始处理任务 %s", task_id)

        # ── 执行业务逻辑（Handler 调度） ──
        payload = dict(task_data.get("input", {}))
        payload["task_id"] = task_id
        result = handler_registry.dispatch(
                task_type=task_data.get("task_type", ""),
                payload=payload,
                            )

        elapsed = time.time() - start_time

        # 标记完成
        db.mark_completed(task_id, result, round(elapsed, 3))

        # 通知结果队列
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps(
                build_result_notification(
                    task_id,
                    "completed",
                    SERVICE_ID,
                    task_data,
                    result,
                )
            ),
        )

        logger.info("任务 %s 完成，耗时 %.2fs", task_id, elapsed)

    except TaskTimeoutError:
        error_msg = "任务处理超时（超过20分钟）"
        elapsed = round(time.time() - start_time, 3)
        db.mark_failed(
            task_id,
            error_msg,
            output=build_failure_output(error_msg, code=3),
            processing_time=elapsed,
        )
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps(
                build_result_notification(
                    task_id, 
                    "failed", 
                    SERVICE_ID, 
                    task_data
                )
            ),
        )
        logger.error("任务 %s 超时失败", task_id)

    except LLMServiceError as e:
        error_msg = str(e)
        elapsed = round(time.time() - start_time, 3)
        db.mark_failed(
            task_id,
            error_msg,
            output=build_failure_output(error_msg, code=LLMServiceError.error_code),
            processing_time=elapsed,
        )
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps(
                build_result_notification(
                    task_id, 
                    "failed", 
                    SERVICE_ID, 
                    task_data
                )
            ),
        )
        logger.error("任务 %s 大模型调用异常: %s", task_id, e)

    except (CompanyProfileConfigurationError, CompanyNotFoundError, ValueError) as e:
        error_msg = str(e)
        elapsed = round(time.time() - start_time, 3)
        db.mark_failed(
            task_id,
            error_msg,
            output=build_failure_output(error_msg, code=2),
            processing_time=elapsed,
        )
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps(
                build_result_notification(
                    task_id, 
                    "failed", 
                    SERVICE_ID, 
                    task_data
                )
            ),
        )
        logger.error("任务 %s 调用失败: %s", task_id, e)

    except Exception as e:
        error_msg = str(e)
        elapsed = round(time.time() - start_time, 3)
        db.mark_failed(
            task_id,
            error_msg,
            output=build_failure_output(error_msg),
            processing_time=elapsed,
        )
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps(
                build_result_notification(
                    task_id, 
                    "failed", 
                    SERVICE_ID, 
                    task_data
                )
            ),
        )
        logger.error("任务 %s 失败: %s", task_id, e)

    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ── 主循环 ────────────────────────────────────────────────
def main():
    global running

    # 加载业务 Handler
    handler_registry = build_handler_registry()

    # 解析每日访问限额：负数/非整数视为非法，告警并退出
    limit, unlimited, quota_err = parse_quota_limit(DAILY_VISIT_LIMIT)
    if quota_err:
        logger.error("DAILY_VISIT_LIMIT 配置非法，拒绝启动（需为 0/空=不限 或正整数）：%s", quota_err)
        try:
            write_quota_meta(redis_client, SERVICE_NAME, limit, unlimited, quota_err, QUOTA_TIMEZONE)
        except Exception:
            pass
        sys.exit(1)

    # 注册服务 + 连接数据库
    register_service()
    db.connect()
    logger.info("开始消费队列: %s", TASK_QUEUE)
    logger.info("每日访问限额：%s", "不限" if unlimited else f"{limit} 次")
    write_quota_meta(redis_client, SERVICE_NAME, limit, unlimited, quota_err, QUOTA_TIMEZONE)
    quota_waiter = None if unlimited else QuotaWaiter(
        redis_client,
        SERVICE_NAME,
        QUOTA_TIMEZONE,
    )

    # 启动独立心跳线程（不受任务阻塞影响）
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    logger.info("心跳线程已启动")

    # 主消费循环
    quota_limit_attempts = 0
    while running:
        try:
            if unlimited:
                result = redis_client.brpop(TASK_QUEUE, timeout=1)
                if result:
                    _, task_json = result
                    task_data = json.loads(task_json)
                    process_job(task_data, handler_registry)
                continue

            task_data, over_limit = pop_limited_task(
                redis_client,
                TASK_QUEUE,
                SERVICE_NAME,
                QUOTA_TIMEZONE,
                limit,
            )
            if over_limit:
                sleep_seconds, sleep_stage = quota_limit_sleep_plan(quota_limit_attempts)
                if should_log_quota_sleep(redis_client, SERVICE_NAME, sleep_stage):
                    logger.info(
                        "每日访问额度已用尽，任务已放回队列，最长等待 %s 秒",
                        sleep_seconds,
                    )
                wake_reason = quota_waiter.wait(sleep_seconds)
                if wake_reason == WAKE_TIMEOUT:
                    quota_limit_attempts += 1
                else:
                    quota_limit_attempts = 0
                    logger.info("额度已刷新，立即重试：%s", wake_reason)
                continue
            if not task_data:
                continue
            quota_limit_attempts = 0
            process_job(task_data, handler_registry)

        except redis.ConnectionError:
            logger.warning("Redis 断开，5秒后重连...")
            time.sleep(5)
        except Exception as e:
            logger.error("错误: %s", e)
            time.sleep(1)

    # 退出
    unregister_service()
    db.close()
    if quota_waiter is not None:
        quota_waiter.close()
    redis_client.close()
    logger.info("服务已退出")


if __name__ == "__main__":
    from common.logging_config import setup_logging
    setup_logging()
    main()
