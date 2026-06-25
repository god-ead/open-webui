#!/usr/bin/env python3
"""
任务数据导出脚本：读取 PostgreSQL tasks 表，保存为 JSON

用法:
    python export_tasks.py                    # 导出所有任务
    python export_tasks.py --status failed    # 只导出失败任务
    python export_tasks.py --status processing --limit 10
    python export_tasks.py -o my_tasks.json   # 指定输出文件名

依赖:
    pip install psycopg2-binary
"""
import os
import json
import logging
import argparse
from datetime import datetime

logger = logging.getLogger("company_profile.export_tasks")

try:
    import psycopg2
except ImportError:
    logger.error("缺少依赖，请执行: pip install psycopg2-binary")
    exit(1)


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/appdb"
)


class DateTimeEncoder(json.JSONEncoder):
    """处理 datetime 序列化"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(obj)


def export_tasks(output_file: str, status: str = None, limit: int = None):
    """导出任务数据"""
    logger.info("连接 %s", DATABASE_URL.replace('postgres:postgres', '***:***'))

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    sql = """
        SELECT id, task_type, input, status, output, error_msg,
               worker_id, queue_position, processing_time,
               created_at, started_at, completed_at
        FROM tasks
    """
    params = []
    conditions = []

    if status:
        conditions.append("status = %s")
        params.append(status)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += " ORDER BY created_at DESC"

    if limit:
        sql += f" LIMIT {limit}"

    cur.execute(sql, params)
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    tasks = []
    for row in rows:
        item = {}
        for col, val in zip(columns, row):
            # JSONB 字段自动解析
            if col in ("input", "output") and val is not None:
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except:
                        pass
                elif isinstance(val, dict):
                    pass  # psycopg2 已解析为 dict
            item[col] = val
        tasks.append(item)

    # 写入文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)

    # 统计
    status_counts = {}
    for t in tasks:
        s = t.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    logger.info("导出完成: %s", output_file)
    logger.info("共 %d 条任务", len(tasks))
    if status_counts:
        logger.info("状态分布: %s", ', '.join(f'{k}={v}' for k, v in status_counts.items()))

    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='导出 tasks 表数据为 JSON')
    parser.add_argument('-o', '--output', default='tasks.json', help='输出文件名 (默认: tasks.json)')
    parser.add_argument('--status', choices=['queued', 'processing', 'completed', 'failed'],
                        help='按状态筛选')
    parser.add_argument('--limit', type=int, help='限制导出条数（默认全部）')
    parser.add_argument('--dsn', help='数据库连接串 (默认从 DATABASE_URL 环境变量读取)')
    args = parser.parse_args()

    if args.dsn:
        global DATABASE_URL
        DATABASE_URL = args.dsn

    export_tasks(args.output, args.status, args.limit)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    main()
