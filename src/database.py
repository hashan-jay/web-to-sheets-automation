from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from src.mapper import collect_brand_tags, is_noise_brand_token
from src.models import Transaction

_TXN_FIELDS = {item.name for item in fields(Transaction)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'dashboard',
    copy_status TEXT NOT NULL DEFAULT 'pending',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_status
    ON notifications (copy_status, created_at);
"""


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _transaction_from_payload(payload: str) -> Transaction:
    data = json.loads(payload)
    data.setdefault("extras", {})
    tags = data.get("tags")
    if isinstance(tags, str):
        data["tags"] = [tags] if tags.strip() else []
    elif not isinstance(tags, list):
        data.pop("tags", None)
    return Transaction(**{key: data[key] for key in _TXN_FIELDS if key in data})


class GatheringDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def ingest(self, transactions: list[Transaction], source: str = "dashboard") -> int:
        inserted = 0
        with self._connect() as conn:
            for txn in transactions:
                if not txn.transaction_id:
                    continue
                try:
                    conn.execute(
                        """
                        INSERT INTO notifications
                            (transaction_id, payload_json, source, copy_status, created_at)
                        VALUES (?, ?, ?, 'pending', ?)
                        """,
                        (
                            txn.transaction_id,
                            json.dumps(asdict(txn)),
                            source,
                            _now(),
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    existing = conn.execute(
                        "SELECT payload_json FROM notifications WHERE transaction_id = ?",
                        (txn.transaction_id,),
                    ).fetchone()
                    if not existing:
                        continue
                    data = json.loads(existing["payload_json"])
                    incoming = asdict(txn)
                    extras = data.get("extras") or {}
                    extras.update(incoming.pop("extras") or {})
                    incoming_tags = incoming.pop("tags", None) or []
                    incoming_brand = incoming.pop("brand", "") or ""
                    for key, value in incoming.items():
                        if not value:
                            continue
                        if not data.get(key):
                            data[key] = value
                    if incoming_brand and not is_noise_brand_token(incoming_brand):
                        current = str(data.get("brand") or "")
                        if not current or is_noise_brand_token(current):
                            data["brand"] = incoming_brand
                        else:
                            data["brand"] = incoming_brand
                    merged_tags = collect_brand_tags(
                        data.get("tags"),
                        incoming_tags,
                        data.get("brand"),
                        incoming_brand,
                    )
                    if merged_tags:
                        data["tags"] = merged_tags
                    data["extras"] = extras
                    conn.execute(
                        "UPDATE notifications SET payload_json = ? WHERE transaction_id = ?",
                        (json.dumps(data), txn.transaction_id),
                    )
        return inserted

    def pending(self) -> list[Transaction]:
        return self.by_status("pending")

    def by_status(self, *statuses: str) -> list[Transaction]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json FROM notifications
                WHERE copy_status IN ({placeholders})
                ORDER BY id
                """,
                statuses,
            ).fetchall()
        return [_transaction_from_payload(row["payload_json"]) for row in rows]

    def records_by_status(self, *statuses: str) -> list[dict]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT transaction_id, source, copy_status, detail, created_at, processed_at, payload_json
                FROM notifications
                WHERE copy_status IN ({placeholders})
                ORDER BY id DESC
                """,
                statuses,
            ).fetchall()
        return [dict(row) for row in rows]

    def reset_failed(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE notifications
                SET copy_status = 'pending', detail = '', processed_at = NULL
                WHERE copy_status = 'failed'
                """
            )
            return int(cur.rowcount)

    def mark(self, transaction_id: str, status: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notifications
                SET copy_status = ?, detail = ?, processed_at = ?
                WHERE transaction_id = ?
                """,
                (status, detail, _now(), transaction_id),
            )

    def known_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT transaction_id FROM notifications").fetchall()
        return {str(row["transaction_id"]) for row in rows if row["transaction_id"]}

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT copy_status, COUNT(*) AS total
                FROM notifications
                GROUP BY copy_status
                """
            ).fetchall()
        result = {"pending": 0, "copied": 0, "skipped": 0, "failed": 0, "preview": 0}
        for row in rows:
            result[row["copy_status"]] = int(row["total"])
        return result

    def recent(self, limit: int = 50) -> list[dict]:
        return self.all_records(limit=limit)

    def all_records(self, limit: int | None = None) -> list[dict]:
        sql = """
            SELECT transaction_id, source, copy_status, detail, created_at, processed_at, payload_json
            FROM notifications
            ORDER BY id DESC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def reset_to_pending(self, transaction_ids: list[str]) -> int:
        ids = [item for item in transaction_ids if item]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE notifications
                SET copy_status = 'pending', detail = '', processed_at = NULL
                WHERE transaction_id IN ({placeholders})
                  AND copy_status = 'failed'
                """,
                ids,
            )
            return int(cur.rowcount)

    def requeue(self, transaction_ids: list[str]) -> int:
        """Put these IDs back in the to-send queue, even if they were marked sent."""
        ids = [item for item in transaction_ids if item]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE notifications
                SET copy_status = 'pending', detail = '', processed_at = NULL
                WHERE transaction_id IN ({placeholders})
                """,
                ids,
            )
            return int(cur.rowcount)

    def delete_ids(self, transaction_ids: list[str]) -> int:
        ids = [item for item in transaction_ids if item]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM notifications WHERE transaction_id IN ({placeholders})",
                ids,
            )
            return int(cur.rowcount)

    def by_ids(self, transaction_ids: list[str]) -> list[Transaction]:
        ids = [item for item in transaction_ids if item]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json FROM notifications
                WHERE transaction_id IN ({placeholders})
                ORDER BY id
                """,
                ids,
            ).fetchall()
        return [_transaction_from_payload(row["payload_json"]) for row in rows]
