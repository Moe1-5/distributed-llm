"""Durable, bounded participant-side settlement submission outbox."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from incentives.protocol import canonical_json, hash_document

SCHEMA_VERSION = 1
ACTIVE_STATES = ("pending", "retrying")


@dataclass(frozen=True)
class OutboxSubmission:
    idempotency_key: str
    payload: dict[str, Any]
    attempt: int
    expires_at: int


def default_outbox_path(identity_path: Path) -> Path:
    configured = os.environ.get("DISTRIBLLM_SETTLEMENT_OUTBOX_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().absolute()
    return identity_path.with_name("settlement-outbox.sqlite3")


def _submission_timestamps(payload: dict[str, Any]) -> list[int]:
    timestamps: list[int] = []
    for field in (
        "worker_receipt",
        "generator_acceptance",
        "worker_presence",
        "generator_presence",
    ):
        document = payload.get(field)
        signed_payload = document.get("payload") if isinstance(document, dict) else None
        timestamp = signed_payload.get("timestamp") if isinstance(signed_payload, dict) else None
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            timestamps.append(timestamp)
    return timestamps


class SubmissionOutbox:
    def __init__(
        self,
        path: str | Path,
        *,
        capacity: int,
        retention_rows: int = 4096,
        timestamp_window_seconds: int = 300,
        expiry_safety_seconds: int = 15,
        owner_public_key: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if retention_rows < 0:
            raise ValueError("retention_rows must be non-negative")
        if timestamp_window_seconds < 1:
            raise ValueError("timestamp_window_seconds must be at least 1")
        if not 0 <= expiry_safety_seconds < timestamp_window_seconds:
            raise ValueError("expiry_safety_seconds must be within the timestamp window")
        if not owner_public_key.strip():
            raise ValueError("owner_public_key must not be empty")
        self.path = Path(path).expanduser().absolute()
        self.capacity = capacity
        self.retention_rows = retention_rows
        self.timestamp_window_seconds = timestamp_window_seconds
        self.expiry_safety_seconds = expiry_safety_seconds
        self.owner_public_key = owner_public_key.strip()
        self._clock = clock
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise RuntimeError("Settlement outbox must be a regular file")
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox_meta (
                    schema_version INTEGER NOT NULL,
                    owner_public_key TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT schema_version, owner_public_key FROM outbox_meta LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO outbox_meta(schema_version, owner_public_key) VALUES (?, ?)",
                    (SCHEMA_VERSION, self.owner_public_key),
                )
            elif int(row["schema_version"]) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported settlement outbox schema version {row['schema_version']}"
                )
            elif str(row["owner_public_key"]) != self.owner_public_key:
                raise RuntimeError("Settlement outbox belongs to a different application identity")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK(state IN ('pending', 'retrying', 'accepted', 'rejected')),
                    attempt INTEGER NOT NULL CHECK(attempt >= 1),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    terminal_reason_json TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS submissions_state_idx ON submissions(state, updated_at)"
            )
        os.chmod(self.path, 0o600)

    def _expiry(self, payload: dict[str, Any], now: int) -> int:
        timestamps = _submission_timestamps(payload)
        origin = min(timestamps) if timestamps else now
        return origin + self.timestamp_window_seconds - self.expiry_safety_seconds

    def enqueue(
        self, payload: dict[str, Any], *, now: int | None = None
    ) -> Literal["inserted", "active", "accepted", "rejected", "full"]:
        timestamp = int(self._clock()) if now is None else int(now)
        serialized = canonical_json(payload).decode("utf-8")
        key = hash_document(payload)
        expires_at = self._expiry(payload, timestamp)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT payload_json, state FROM submissions WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if prior is not None:
                if prior["payload_json"] != serialized:
                    return "rejected"
                if prior["state"] in ACTIVE_STATES:
                    return "active"
                return "accepted" if prior["state"] == "accepted" else "rejected"
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM submissions WHERE state IN ('pending', 'retrying')"
                ).fetchone()[0]
            )
            if active >= self.capacity:
                return "full"
            connection.execute(
                """
                INSERT INTO submissions(
                    idempotency_key, payload_json, state, attempt,
                    created_at, updated_at, expires_at, terminal_reason_json
                ) VALUES (?, ?, 'pending', 1, ?, ?, ?, NULL)
                """,
                (key, serialized, timestamp, timestamp, expires_at),
            )
        return "inserted"

    def recover(self, *, now: int | None = None) -> tuple[list[OutboxSubmission], int]:
        timestamp = int(self._clock()) if now is None else int(now)
        expired = 0
        recovered: list[OutboxSubmission] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT idempotency_key, payload_json, attempt, expires_at
                FROM submissions
                WHERE state IN ('pending', 'retrying')
                ORDER BY created_at, idempotency_key
                """
            ).fetchall()
            for row in rows:
                if int(row["expires_at"]) <= timestamp:
                    reason = json.dumps(
                        {
                            "code": "receipt_expired_before_submission",
                            "message": "Receipt expired before a safe settlement retry.",
                            "attempts": int(row["attempt"]),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        UPDATE submissions
                        SET state = 'rejected', updated_at = ?, terminal_reason_json = ?
                        WHERE idempotency_key = ?
                        """,
                        (timestamp, reason, row["idempotency_key"]),
                    )
                    expired += 1
                    continue
                recovered.append(
                    OutboxSubmission(
                        idempotency_key=str(row["idempotency_key"]),
                        payload=json.loads(str(row["payload_json"])),
                        attempt=int(row["attempt"]),
                        expires_at=int(row["expires_at"]),
                    )
                )
            self._prune_locked(connection)
        return recovered, expired

    def mark_retry(self, key: str, *, attempt: int, message: str) -> None:
        self._update(
            key,
            state="retrying",
            attempt=attempt,
            reason={
                "code": "transient_settlement_failure",
                "message": message,
                "attempts": attempt,
            },
        )

    def mark_accepted(self, key: str, *, attempt: int) -> None:
        self._update(key, state="accepted", attempt=attempt, reason=None)

    def mark_rejected(self, key: str, *, attempt: int, code: str, message: str) -> None:
        self._update(
            key,
            state="rejected",
            attempt=attempt,
            reason={"code": code, "message": message, "attempts": attempt},
        )

    def _update(
        self,
        key: str,
        *,
        state: str,
        attempt: int,
        reason: dict[str, Any] | None,
    ) -> None:
        timestamp = int(self._clock())
        encoded_reason = (
            None
            if reason is None
            else json.dumps(reason, sort_keys=True, separators=(",", ":"))
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE submissions
                SET state = ?, attempt = ?, updated_at = ?, terminal_reason_json = ?
                WHERE idempotency_key = ?
                """,
                (state, attempt, timestamp, encoded_reason, key),
            )
            self._prune_locked(connection)

    def _prune_locked(self, connection: sqlite3.Connection) -> None:
        if self.retention_rows == 0:
            connection.execute(
                "DELETE FROM submissions WHERE state IN ('accepted', 'rejected')"
            )
            return
        connection.execute(
            """
            DELETE FROM submissions
            WHERE state IN ('accepted', 'rejected')
              AND idempotency_key NOT IN (
                SELECT idempotency_key FROM submissions
                WHERE state IN ('accepted', 'rejected')
                ORDER BY updated_at DESC, idempotency_key DESC
                LIMIT ?
              )
            """,
            (self.retention_rows,),
        )

    def summary(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM submissions GROUP BY state"
                )
            }
        return {
            "pending": counts.get("pending", 0) + counts.get("retrying", 0),
            "accepted": counts.get("accepted", 0),
            "rejected": counts.get("rejected", 0),
        }
