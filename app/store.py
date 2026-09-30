from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import IngestMessage, SenderKind


class MessageStore:
    """本地 SQLite：消息去重入库 + 按群取最近对话。"""

    def __init__(self, data_dir: str) -> None:
        self._lock = threading.Lock()
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._db = path / "messages.db"
        self._state = path / "state.json"
        if not self._state.exists():
            self._state.write_text("{}", encoding="utf-8")
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    msg_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    room_name TEXT,
                    sender_id TEXT,
                    sender_name TEXT,
                    sender_kind TEXT,
                    content TEXT,
                    sent_at REAL NOT NULL,
                    msg_type TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_room_time ON messages(room_id, sent_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_messages(self, messages: List[IngestMessage]) -> int:
        inserted = 0
        with self._lock, self._connect() as conn:
            for m in messages:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                    (msg_id, room_id, room_name, sender_id, sender_name, sender_kind,
                     content, sent_at, msg_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        m.msg_id,
                        m.room_id,
                        m.room_name,
                        m.sender_id,
                        m.sender_name,
                        (m.sender_kind.value if m.sender_kind else SenderKind.unknown.value),
                        m.content,
                        float(m.sent_at),
                        m.msg_type,
                    ),
                )
                inserted += cur.rowcount
            conn.commit()
        return inserted

    def list_room_ids(self) -> List[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT room_id FROM messages").fetchall()
        return [r["room_id"] for r in rows]

    def recent_messages(self, room_id: str, limit: int = 30) -> List[IngestMessage]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE room_id = ?
                ORDER BY sent_at DESC
                LIMIT ?
                """,
                (room_id, limit),
            ).fetchall()
        items = [self._row_to_msg(r) for r in rows]
        items.reverse()
        return items

    def clear_all(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM messages")
            conn.commit()

    def get_alert_at(self, key: str) -> Optional[float]:
        data = self._load_state()
        alerts = data.get("alerts") or {}
        val = alerts.get(key)
        return float(val) if val is not None else None

    def set_alert_at(self, key: str, ts: Optional[float] = None) -> None:
        data = self._load_state()
        alerts = data.setdefault("alerts", {})
        alerts[key] = float(ts if ts is not None else time.time())
        self._save_state(data)

    def _load_state(self) -> Dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self._state.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError:
                return {}

    def _save_state(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._state.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> IngestMessage:
        kind = row["sender_kind"] or SenderKind.unknown.value
        try:
            sender_kind = SenderKind(kind)
        except ValueError:
            sender_kind = SenderKind.unknown
        return IngestMessage(
            msg_id=row["msg_id"],
            room_id=row["room_id"],
            room_name=row["room_name"] or "",
            sender_id=row["sender_id"] or "",
            sender_name=row["sender_name"] or "",
            sender_kind=sender_kind,
            content=row["content"] or "",
            sent_at=float(row["sent_at"]),
            msg_type=row["msg_type"] or "text",
        )
