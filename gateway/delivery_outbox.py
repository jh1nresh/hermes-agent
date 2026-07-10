"""Profile-local durable state for outbound platform deliveries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home


_STATES = frozenset({"pending", "dispatched", "confirmed", "unknown", "failed"})


class UnknownDeliveryError(RuntimeError):
    """Raised when a possibly-sent delivery cannot safely be retried."""


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    origin: str
    destination: str
    payload_hash: str
    state: str
    attempt: int
    created_at: str
    updated_at: str
    dispatched_at: Optional[str]
    confirmed_at: Optional[str]
    provider_receipt: Optional[dict[str, Any]]
    last_error: Optional[str]


class DeliveryOutbox:
    """Small SQLite outbox recording certainty around non-idempotent sends."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else get_hermes_home() / "gateway" / "delivery_outbox.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','dispatched','confirmed','unknown','failed')),
                attempt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                dispatched_at TEXT,
                confirmed_at TEXT,
                provider_receipt TEXT,
                last_error TEXT
            )
            """
        )
        self._conn.commit()
        self.reconcile_restart()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def hash_payload(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _record(row: sqlite3.Row) -> DeliveryRecord:
        values = dict(row)
        receipt = values.pop("provider_receipt")
        values["provider_receipt"] = json.loads(receipt) if receipt else None
        return DeliveryRecord(**values)

    def close(self) -> None:
        self._conn.close()

    def reconcile_restart(self) -> int:
        """Make crash-interrupted sends explicitly uncertain, never retryable."""
        now = self._now()
        cursor = self._conn.execute(
            "UPDATE deliveries SET state='unknown', updated_at=? WHERE state='dispatched'",
            (now,),
        )
        self._conn.commit()
        return cursor.rowcount

    def get(self, delivery_id: str) -> Optional[DeliveryRecord]:
        row = self._conn.execute(
            "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,)
        ).fetchone()
        return self._record(row) if row else None

    def create(
        self,
        delivery_id: str,
        origin: str,
        destination: str,
        payload: str,
    ) -> DeliveryRecord:
        payload_hash = self.hash_payload(payload)
        existing = self.get(delivery_id)
        if existing:
            identity = (origin, destination, payload_hash)
            if identity != (existing.origin, existing.destination, existing.payload_hash):
                raise ValueError(f"delivery id {delivery_id!r} already identifies a different delivery")
            return existing
        now = self._now()
        self._conn.execute(
            "INSERT INTO deliveries "
            "(delivery_id, origin, destination, payload_hash, state, attempt, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (delivery_id, origin, destination, payload_hash, now, now),
        )
        self._conn.commit()
        return self.get(delivery_id)  # type: ignore[return-value]

    def _transition(
        self,
        delivery_id: str,
        state: str,
        *,
        receipt: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> DeliveryRecord:
        if state not in _STATES:
            raise ValueError(f"invalid delivery state: {state}")
        now = self._now()
        dispatched_at = now if state == "dispatched" else None
        confirmed_at = now if state == "confirmed" else None
        increment = 1 if state == "dispatched" else 0
        self._conn.execute(
            "UPDATE deliveries SET state=?, attempt=attempt+?, updated_at=?, "
            "dispatched_at=COALESCE(?, dispatched_at), confirmed_at=COALESCE(?, confirmed_at), "
            "provider_receipt=COALESCE(?, provider_receipt), last_error=? WHERE delivery_id=?",
            (
                state,
                increment,
                now,
                dispatched_at,
                confirmed_at,
                json.dumps(receipt, sort_keys=True, default=str) if receipt is not None else None,
                error,
                delivery_id,
            ),
        )
        self._conn.commit()
        record = self.get(delivery_id)
        if record is None:
            raise KeyError(delivery_id)
        return record

    def mark_dispatched(self, delivery_id: str) -> DeliveryRecord:
        return self._transition(delivery_id, "dispatched")

    def mark_confirmed(self, delivery_id: str, receipt: Optional[dict[str, Any]] = None) -> DeliveryRecord:
        return self._transition(delivery_id, "confirmed", receipt=receipt)

    def mark_unknown(self, delivery_id: str, error: Optional[str] = None) -> DeliveryRecord:
        return self._transition(delivery_id, "unknown", error=error)

    def mark_failed(self, delivery_id: str, error: str) -> DeliveryRecord:
        return self._transition(delivery_id, "failed", error=error)

    def retryable(self, delivery_id: str) -> bool:
        record = self.get(delivery_id)
        return bool(record and record.state in {"pending", "failed"})
