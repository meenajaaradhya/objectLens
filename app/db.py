import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                telegram_id INTEGER UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS link_requests (
                code TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                phone TEXT NOT NULL,
                session_token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                verified_at TEXT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                pending_summary TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                object_name TEXT,
                created_at TEXT NOT NULL,
                ended_at TEXT,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_id_idx
                ON messages(conversation_id, id);
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(link_requests)").fetchall()
        }
        if "user_id" not in columns:
            conn.execute(
                "ALTER TABLE link_requests ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
            )
        if "pending_summary" not in columns:
            conn.execute("ALTER TABLE link_requests ADD COLUMN pending_summary TEXT")


def link_request_is_expired(created_at: str) -> bool:
    cutoff = utc_now() - timedelta(minutes=settings.link_code_ttl_minutes)
    return datetime.fromisoformat(created_at) < cutoff
