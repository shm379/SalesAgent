"""Tiny synchronous sqlite3 helper for the bot_threads table - a small parallel
table (separate from the LangGraph checkpointer) that tracks just enough per
chat to (a) drive the 1h/2d follow-up jobs and (b) reconcile pending jobs
after a bot restart, without having to parse LangGraph's internal checkpoint
blobs for scheduling purposes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_threads (
    chat_id TEXT PRIMARY KEY,
    last_user_message_at TEXT NOT NULL,
    purchase_confirmed INTEGER NOT NULL DEFAULT 0,
    discount_code TEXT
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_thread(db_path: Path, chat_id: str, last_user_message_at: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO bot_threads (chat_id, last_user_message_at, purchase_confirmed)
            VALUES (?, ?, 0)
            ON CONFLICT(chat_id) DO UPDATE SET last_user_message_at = excluded.last_user_message_at
            """,
            (chat_id, last_user_message_at),
        )
        conn.commit()
    finally:
        conn.close()


def set_purchase_confirmed(db_path: Path, chat_id: str, confirmed: bool = True) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE bot_threads SET purchase_confirmed = ? WHERE chat_id = ?",
            (1 if confirmed else 0, chat_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_discount_code(db_path: Path, chat_id: str, code: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE bot_threads SET discount_code = ? WHERE chat_id = ?", (code, chat_id))
        conn.commit()
    finally:
        conn.close()


def get_thread(db_path: Path, chat_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT chat_id, last_user_message_at, purchase_confirmed, discount_code "
            "FROM bot_threads WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "chat_id": row[0],
        "last_user_message_at": row[1],
        "purchase_confirmed": bool(row[2]),
        "discount_code": row[3],
    }


def get_all_threads(db_path: Path) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chat_id, last_user_message_at, purchase_confirmed, discount_code FROM bot_threads"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "chat_id": r[0],
            "last_user_message_at": r[1],
            "purchase_confirmed": bool(r[2]),
            "discount_code": r[3],
        }
        for r in rows
    ]
