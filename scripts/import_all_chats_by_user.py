#!/usr/bin/env python3
from __future__ import annotations

"""Import an Open WebUI all-chats export while preserving source user_id.

This is intentionally a small SQLite utility for one-off inspection imports.
It creates non-login placeholder users for source user IDs missing from the
target DB, then inserts chats and chat_message rows under their original users.
"""

import argparse
import collections
import datetime as dt
import json
import shutil
import sqlite3
import time
from pathlib import Path


def load_export(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("export JSON must be a list of chats")
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "chat" not in item or "user_id" not in item:
            raise SystemExit(f"invalid chat export item at index {i}")
    return data


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def summarize(chats: list[dict]) -> None:
    by_user = collections.defaultdict(list)
    for chat in chats:
        by_user[chat["user_id"]].append(chat)

    print(f"total_chats={len(chats)} distinct_users={len(by_user)}")
    for user_id, user_chats in sorted(by_user.items(), key=lambda item: (-len(item[1]), item[0])):
        first = min((c.get("created_at") or 0) for c in user_chats)
        last = max((c.get("updated_at") or c.get("created_at") or 0) for c in user_chats)
        first_s = dt.datetime.fromtimestamp(first).strftime("%Y-%m-%d") if first else "-"
        last_s = dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d") if last else "-"
        titles = " | ".join((c.get("title") or "New Chat") for c in user_chats[:3])
        print(f"{user_id} chats={len(user_chats)} range={first_s}..{last_s} titles={titles}")


def backup_db(db_path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-import-by-user-{stamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def ensure_user(cur: sqlite3.Cursor, user_id: str, now: int) -> bool:
    exists = cur.execute("SELECT 1 FROM user WHERE id = ?", (user_id,)).fetchone()
    if exists:
        return False

    short_id = user_id.split("-")[0]
    cur.execute(
        """
        INSERT INTO user (
            id, name, email, role, profile_image_url,
            created_at, updated_at, last_active_at,
            username, bio, gender, date_of_birth, profile_banner_image_url,
            timezone, presence_state, status_emoji, status_message,
            status_expires_at, oauth, info, settings, scim
        )
        VALUES (?, ?, ?, 'user', '/user.png', ?, ?, 0, NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL, NULL, 'null', 'null', '{}', 'null')
        """,
        (
            user_id,
            f"imported-{short_id}",
            f"imported-{short_id}@local.invalid",
            now,
            now,
        ),
    )
    return True


def insert_chat(cur: sqlite3.Cursor, item: dict) -> bool:
    chat_id = item["id"]
    exists = cur.execute("SELECT 1 FROM chat WHERE id = ?", (chat_id,)).fetchone()
    if exists:
        return False

    chat = item["chat"]
    title = item.get("title") or chat.get("title") or "New Chat"
    cur.execute(
        """
        INSERT INTO chat (
            id, user_id, title, share_id, archived, created_at, updated_at,
            chat, pinned, meta, folder_id
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            item["user_id"],
            title,
            1 if item.get("archived") else 0,
            item.get("created_at") or int(time.time()),
            item.get("updated_at") or item.get("created_at") or int(time.time()),
            json_dumps(chat),
            1 if item.get("pinned") else 0,
            json_dumps(item.get("meta") or {}),
            item.get("folder_id"),
        ),
    )
    return True


def insert_messages(cur: sqlite3.Cursor, item: dict) -> int:
    chat = item["chat"]
    messages = ((chat.get("history") or {}).get("messages") or {})
    inserted = 0
    now = int(time.time())
    for message_id, message in messages.items():
        if not isinstance(message, dict) or not message.get("role"):
            continue
        composite_id = f"{item['id']}-{message_id}"
        exists = cur.execute("SELECT 1 FROM chat_message WHERE id = ?", (composite_id,)).fetchone()
        if exists:
            continue
        usage = message.get("usage")
        if not usage:
            info = message.get("info") or {}
            usage = info.get("usage")
        cur.execute(
            """
            INSERT INTO chat_message (
                id, chat_id, user_id, role, parent_id, content, output, model_id,
                files, sources, embeds, done, status_history, error, usage,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                composite_id,
                item["id"],
                item["user_id"],
                message.get("role") or "user",
                message.get("parent_id") or message.get("parentId"),
                json_dumps(message.get("content")) if "content" in message else None,
                json_dumps(message.get("output")) if "output" in message else None,
                message.get("model_id") or message.get("model"),
                json_dumps(message.get("files")) if "files" in message else None,
                json_dumps(message.get("sources")) if "sources" in message else None,
                json_dumps(message.get("embeds")) if "embeds" in message else None,
                1 if message.get("done", True) else 0,
                json_dumps(message.get("status_history") or message.get("statusHistory"))
                if (message.get("status_history") or message.get("statusHistory")) is not None
                else None,
                json_dumps(message.get("error")) if "error" in message else None,
                json_dumps(usage) if usage else None,
                message.get("timestamp") or item.get("created_at") or now,
                now,
            ),
        )
        inserted += 1
    return inserted


def import_chats(db_path: Path, chats: list[dict]) -> None:
    backup_path = backup_db(db_path)
    print(f"backup={backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        now = int(time.time())
        users_created = 0
        chats_inserted = 0
        chats_skipped = 0
        messages_inserted = 0

        with conn:
            for user_id in sorted({chat["user_id"] for chat in chats}):
                users_created += 1 if ensure_user(cur, user_id, now) else 0

            for item in chats:
                if insert_chat(cur, item):
                    chats_inserted += 1
                    messages_inserted += insert_messages(cur, item)
                else:
                    chats_skipped += 1

        print(
            "result "
            f"users_created={users_created} "
            f"chats_inserted={chats_inserted} "
            f"chats_skipped={chats_skipped} "
            f"messages_inserted={messages_inserted}"
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_json", type=Path)
    parser.add_argument("--db", type=Path, default=Path("/data/app/sales-chatbot/webui.db"))
    parser.add_argument("--apply", action="store_true", help="write to the target DB")
    args = parser.parse_args()

    chats = load_export(args.export_json)
    summarize(chats)
    if args.apply:
        import_chats(args.db, chats)
    else:
        print("dry_run=true; rerun with --apply to import")


if __name__ == "__main__":
    main()
