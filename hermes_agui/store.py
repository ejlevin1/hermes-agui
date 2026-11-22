"""Transcript stores: the bridge's own record of each thread's AG-UI messages, replayed by ``connect`` (D7).

Hermes' API server does expose a public session read (``GET /api/sessions/{id}/messages``,
``gateway/platforms/api_server.py:1582`` -> ``_handle_session_messages`` at :2720), but it returns Hermes' wire rows
(no AG-UI ids, frontend tool calls are not stored with their browser results), so the bridge keeps its own record.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

__all__ = ["InMemoryTranscriptStore", "SqliteTranscriptStore"]


class InMemoryTranscriptStore:
    """Process-local store (tests, throwaway use)."""

    def __init__(self) -> None:
        self._m: dict[str, list[dict]] = defaultdict(list)

    def append(self, thread_key: str, message: dict) -> None:
        self._m[thread_key].append(json.loads(json.dumps(message)))

    def messages(self, thread_key: str) -> list[dict]:
        return list(self._m.get(thread_key, []))


class SqliteTranscriptStore:
    """SQLite-backed store; ``path`` may be ``":memory:"``. Safe to share between threads."""

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_key TEXT NOT NULL, body TEXT NOT NULL)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_key, seq)"
            )

    def append(self, thread_key: str, message: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO messages(thread_key, body) VALUES (?, ?)",
                (thread_key, json.dumps(message)),
            )

    def messages(self, thread_key: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM messages WHERE thread_key = ? ORDER BY seq",
                (thread_key,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
