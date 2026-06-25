""" 业务 Service 数据库操作封装 """

import os
import json

import psycopg2


class Database:
    """任务状态数据库操作封装（psycopg2 同步连接）。"""

    def __init__(self):
        self.dsn = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@postgres:5432/appdb",
        )
        self.conn = None

    # ── 连接管理 ─────────────────────────────────────────
    def connect(self):
        self.conn = psycopg2.connect(self.dsn)

    def close(self):
        if self.conn:
            self.conn.close()

    # ── 状态更新 ─────────────────────────────────────────
    def mark_processing(self, task_id: str, worker_id: str):
        """标记任务为处理中，记录 worker 和开始时间"""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status='processing', worker_id=%s, started_at=NOW() WHERE id=%s",
                (worker_id, task_id),
            )
            self.conn.commit()

    def mark_completed(self, task_id: str, output: dict, processing_time: float):
        """标记任务完成，写入结果和耗时"""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status='completed', output=%s, processing_time=%s, completed_at=NOW() WHERE id=%s",
                (json.dumps(output, ensure_ascii=False), processing_time, task_id),
            )
            self.conn.commit()

    def mark_failed(
        self,
        task_id: str,
        error_msg: str,
        output: dict | None = None,
        processing_time: float | None = None,
    ):
        """标记任务失败，记录错误信息和可选业务输出"""
        with self.conn.cursor() as cur:
            if output is None and processing_time is None:
                cur.execute(
                    "UPDATE tasks SET status='failed', error_msg=%s, completed_at=NOW() WHERE id=%s",
                    (error_msg, task_id),
                )
            else:
                cur.execute(
                    "UPDATE tasks SET status='failed', output=%s, error_msg=%s, processing_time=%s, completed_at=NOW() WHERE id=%s",
                    (
                        json.dumps(output or {}, ensure_ascii=False),
                        error_msg,
                        processing_time,
                        task_id,
                    ),
                )
            self.conn.commit()


# 全局单例 — main.py 中直接 import 使用
db = Database()
