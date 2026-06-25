""" 业务 Service Worker — 通用任务消费入口，从 Redis 中消费任务 """

import os
import json
import time
import signal
import threading

import redis

from app.db import db
from handlers import HandlerRegistry, build_handler_registry

# ── 环境变量 ──────────────────────────────────────────────
SERVICE_ID = os.getenv("SERVICE_ID", "service")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
TASK_QUEUE = os.getenv("TASK_QUEUE", "task_queue")
RESULT_QUEUE = os.getenv("RESULT_QUEUE", "result_queue")

# ── 全局状态 ──────────────────────────────────────────────
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
running = True


def handle_signal(signum, frame):
    global running
    print(f"\n[{SERVICE_ID}] 收到信号 {signum}，准备关闭...")
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
        print(f"[{SERVICE_ID}] 心跳失败: {e}")


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
    print(f"[{SERVICE_ID}] 服务已注册")


def unregister_service():
    redis_client.srem("active_workers", SERVICE_ID)
    redis_client.delete(f"service:{SERVICE_ID}")


# ── 任务超时 ──────────────────────────────────────────────
class TaskTimeoutError(Exception):
    """任务处理超时异常"""
    pass


def _timeout_handler(signum, frame):
    raise TaskTimeoutError("任务处理超过 20 分钟")


def build_failure_output(error_msg: str, version: str = "") -> dict:
    return {
        "code": 3,
        "message": f"企业画像生成失败: {error_msg}",
        "timestamp": int(time.time() * 1000),
        "data": {
            "profile": "",
            "version": version,
        },
    }


# ── 任务处理 ──────────────────────────────────────────────
def process_job(task_data: dict, handler_registry: HandlerRegistry):
    """
    处理单个任务的核心逻辑。

    1. 检查任务状态（跳过已完成/失败/取消的任务）
    2. 标记 processing → 执行 handler → 标记 completed/failed
    3. 推送到 RESULT_QUEUE 触发回调 + WebSocket 通知
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
                print(f"[{SERVICE_ID}] 任务 {task_id} 状态为 {row[0]}，跳过")
                return

        # 标记处理中
        db.mark_processing(task_id, SERVICE_ID)
        print(f"[{SERVICE_ID}] 开始处理任务 {task_id}")

        # ── 执行业务逻辑（Handler 调度） ──
        result = handler_registry.dispatch(
            task_type=task_data.get("task_type", ""),
            payload=task_data.get("input", {}),
        )

        elapsed = time.time() - start_time

        # 标记完成
        db.mark_completed(task_id, result, round(elapsed, 3))

        # 通知结果队列
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps({
                "task_id": task_id,
                "status": "completed",
                "worker": SERVICE_ID,
                "callback": task_data.get("callback"),
            }),
        )

        print(f"[{SERVICE_ID}] 任务 {task_id} 完成，耗时 {elapsed:.2f}s")

    except TaskTimeoutError:
        error_msg = "任务处理超时（超过20分钟）"
        elapsed = round(time.time() - start_time, 3)
        db.mark_failed(
            task_id,
            error_msg,
            output=build_failure_output(error_msg),
            processing_time=elapsed,
        )
        redis_client.rpush(
            RESULT_QUEUE,
            json.dumps({
                "task_id": task_id,
                "status": "failed",
                "worker": SERVICE_ID,
                "callback": task_data.get("callback"),
            }),
        )
        print(f"[{SERVICE_ID}] 任务 {task_id} 超时失败")

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
            json.dumps({
                "task_id": task_id,
                "status": "failed",
                "worker": SERVICE_ID,
                "callback": task_data.get("callback"),
            }),
        )
        print(f"[{SERVICE_ID}] 任务 {task_id} 失败: {e}")

    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ── 主循环 ────────────────────────────────────────────────
def main():
    global running

    # 加载业务 Handler
    handler_registry = build_handler_registry()

    # 注册服务 + 连接数据库
    register_service()
    db.connect()
    print(f"[{SERVICE_ID}] 开始消费队列: {TASK_QUEUE}")

    # 启动独立心跳线程（不受任务阻塞影响）
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    print(f"[{SERVICE_ID}] 心跳线程已启动")

    # 主消费循环
    while running:
        try:
            result = redis_client.brpop(TASK_QUEUE, timeout=1)
            if result:
                _, task_json = result
                task_data = json.loads(task_json)
                process_job(task_data, handler_registry)

        except redis.ConnectionError:
            print(f"[{SERVICE_ID}] Redis 断开，5秒后重连...")
            time.sleep(5)
        except Exception as e:
            print(f"[{SERVICE_ID}] 错误: {e}")
            time.sleep(1)

    # 优雅退出
    unregister_service()
    db.close()
    redis_client.close()
    print(f"[{SERVICE_ID}] 服务已退出")


if __name__ == "__main__":
    main()
