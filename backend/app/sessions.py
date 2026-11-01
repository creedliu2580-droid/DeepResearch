"""会话元数据存储：标题 + 时间，用于历史会话列表（与 LangGraph 检查点分离）。"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

from .settings import get_settings

logger = logging.getLogger(__name__)

_conn: aiosqlite.Connection | None = None


def _db_path() -> str:
    return str(Path(get_settings().server.checkpoint_db_path).with_name("sessions.sqlite"))


async def init() -> None:
    global _conn
    _conn = await aiosqlite.connect(_db_path())
    await _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            thread_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    await _conn.commit()


async def upsert(thread_id: str, title: str) -> None:
    if _conn is None:
        logger.warning("sessions 未初始化，跳过 upsert(%s)", thread_id)
        return
    now = time.time()
    await _conn.execute(
        "INSERT INTO sessions(thread_id, title, created_at, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(thread_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
        (thread_id, title[:80], now, now),
    )
    await _conn.commit()


async def touch(thread_id: str) -> None:
    if _conn is None:
        logger.warning("sessions 未初始化，跳过 touch(%s)", thread_id)
        return
    await _conn.execute(
        "UPDATE sessions SET updated_at=? WHERE thread_id=?",
        (time.time(), thread_id),
    )
    await _conn.commit()


async def list_sessions(limit: int = 50) -> list[dict]:
    if _conn is None:
        logger.warning("sessions 未初始化，返回空列表")
        return []
    cur = await _conn.execute(
        "SELECT thread_id, title, created_at, updated_at FROM sessions "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return [
        {"thread_id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None
