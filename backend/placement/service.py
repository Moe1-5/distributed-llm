"""Durable authoritative coordinator for exclusive model-layer placement."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from client.coverage import (
    build_serving_plan,
    recommend_exclusive_range,
    ranges_overlap,
)
from constants import SUPPORTED_MODELS

SCHEMA_VERSION = 1
ACTIVE_STATES = ("RESERVED", "JOINING", "ONLINE")
TERMINAL_STATES = ("OFFLINE", "EXPIRED")
PlacementState = Literal["RESERVED", "JOINING", "ONLINE", "OFFLINE", "EXPIRED"]
PlacementMode = Literal["recommended", "custom"]


class PlacementError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})

    def response(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": str(self),
            **self.details,
        }


class PlacementConflict(PlacementError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(code, message, status_code=409, details=details)


class ReservationRequest(BaseModel):
    participant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    model_name: str = Field(min_length=1, max_length=256)
    model_revision: str = Field(min_length=1, max_length=128)
    layer_capacity: int = Field(ge=1)
    placement_mode: PlacementMode = "recommended"
    layer_start: Optional[int] = Field(default=None, ge=0)
    layer_end: Optional[int] = Field(default=None, ge=1)
    expected_topology_revision: Optional[int] = Field(default=None, ge=0)

    @field_validator(
        "participant_id",
        "idempotency_key",
        "model_name",
        "model_revision",
    )
    @classmethod
    def nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def custom_range_is_complete(self) -> "ReservationRequest":
        if self.placement_mode == "custom":
            if self.layer_start is None or self.layer_end is None:
                raise ValueError("custom placement requires layer_start and layer_end")
            if self.layer_end <= self.layer_start:
                raise ValueError("layer_end must be greater than layer_start")
            if self.layer_end - self.layer_start != self.layer_capacity:
                raise ValueError("custom range length must equal layer_capacity")
        return self


class ReservationMutation(BaseModel):
    participant_id: str = Field(min_length=1, max_length=128)
    reservation_token: str = Field(min_length=32, max_length=256)

    @field_validator("participant_id", "reservation_token")
    @classmethod
    def mutation_value_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class JoiningMutation(ReservationMutation):
    node_id: str = Field(min_length=1, max_length=128)


class OnlineMutation(ReservationMutation):
    node_id: str = Field(min_length=1, max_length=128)
    peer_id: str = Field(min_length=1, max_length=256)
    rpc_uid: str = Field(min_length=1, max_length=512)
    advertised_peer_id: str = Field(min_length=1, max_length=256)
    advertised_rpc_uid: str = Field(min_length=1, max_length=512)
    model_revision: str = Field(min_length=1, max_length=128)
    advertised_model_revision: str = Field(min_length=1, max_length=128)
    rpc_ready: bool
    publication_ready: bool


class RenewMutation(ReservationMutation):
    peer_id: Optional[str] = Field(default=None, max_length=256)
    rpc_uid: Optional[str] = Field(default=None, max_length=512)


class ReleaseMutation(ReservationMutation):
    reason: str = Field(default="participant_release", min_length=1, max_length=256)


def _utc(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _fingerprint(request: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PlacementStore:
    """SQLite lease state with process-safe atomic allocation transactions."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        token_secret: str,
        startup_ttl_seconds: float = 90.0,
        online_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(token_secret) < 32:
            raise ValueError("placement token_secret must contain at least 32 characters")
        if not 5 <= startup_ttl_seconds <= 3600:
            raise ValueError("startup_ttl_seconds must be between 5 and 3600")
        if not startup_ttl_seconds <= online_ttl_seconds <= 86400:
            raise ValueError(
                "online_ttl_seconds must be at least startup_ttl_seconds and at most 86400"
            )
        self.database_path = Path(database_path).expanduser().resolve()
        self.token_secret = token_secret.encode("utf-8")
        self.startup_ttl_seconds = float(startup_ttl_seconds)
        self.online_ttl_seconds = float(online_ttl_seconds)
        self.clock = clock
        self._schema_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._schema_lock:
            self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.database_path.parent.chmod(0o700)
            except OSError:
                pass
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in {0, SCHEMA_VERSION}:
                    raise RuntimeError(
                        f"Unsupported placement database schema version {version}"
                    )
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS placement_meta (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS reservations (
                        reservation_id TEXT PRIMARY KEY,
                        participant_id TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        request_fingerprint TEXT NOT NULL,
                        token_hash TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        model_revision TEXT NOT NULL,
                        total_layers INTEGER NOT NULL,
                        layer_capacity INTEGER NOT NULL,
                        layer_start INTEGER NOT NULL,
                        layer_end INTEGER NOT NULL,
                        placement_mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        node_id TEXT,
                        peer_id TEXT,
                        rpc_uid TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_heartbeat_at REAL,
                        expires_at REAL NOT NULL,
                        topology_revision INTEGER NOT NULL,
                        terminal_reason TEXT,
                        UNIQUE(participant_id, idempotency_key)
                    );
                    CREATE INDEX IF NOT EXISTS reservation_model_occupancy
                        ON reservations(model_name, model_revision, state, layer_start, layer_end);
                    CREATE INDEX IF NOT EXISTS reservation_expiry
                        ON reservations(state, expires_at);
                    CREATE TABLE IF NOT EXISTS placement_audit (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        reservation_id TEXT,
                        participant_id TEXT,
                        action TEXT NOT NULL,
                        previous_state TEXT,
                        next_state TEXT,
                        topology_revision INTEGER NOT NULL,
                        observed_at REAL NOT NULL,
                        details_json TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT OR IGNORE INTO placement_meta(key, value) VALUES ('topology_revision', 0)"
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            try:
                self.database_path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _revision_locked(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM placement_meta WHERE key='topology_revision'"
        ).fetchone()
        if row is None:
            raise RuntimeError("placement topology revision is missing")
        return int(row["value"])

    @staticmethod
    def _bump_revision_locked(connection: sqlite3.Connection) -> int:
        connection.execute(
            "UPDATE placement_meta SET value=value+1 WHERE key='topology_revision'"
        )
        return PlacementStore._revision_locked(connection)

    @staticmethod
    def _audit_locked(
        connection: sqlite3.Connection,
        *,
        reservation_id: Optional[str],
        participant_id: Optional[str],
        action: str,
        previous_state: Optional[str],
        next_state: Optional[str],
        topology_revision: int,
        now: float,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO placement_audit(
                reservation_id, participant_id, action, previous_state,
                next_state, topology_revision, observed_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation_id,
                participant_id,
                action,
                previous_state,
                next_state,
                topology_revision,
                now,
                json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
            ),
        )

    def _derive_token(self, reservation_id: str, participant_id: str) -> str:
        digest = hmac.new(
            self.token_secret,
            f"placement-v1\0{reservation_id}\0{participant_id}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _expire_locked(self, connection: sqlite3.Connection, now: float) -> int:
        expired = connection.execute(
            """
            SELECT reservation_id, participant_id, state
            FROM reservations
            WHERE state IN ('RESERVED', 'JOINING', 'ONLINE') AND expires_at <= ?
            ORDER BY reservation_id
            """,
            (now,),
        ).fetchall()
        if not expired:
            return self._revision_locked(connection)
        revision = self._bump_revision_locked(connection)
        for row in expired:
            connection.execute(
                """
                UPDATE reservations
                SET state='EXPIRED', updated_at=?, topology_revision=?,
                    terminal_reason='lease_expired'
                WHERE reservation_id=?
                """,
                (now, revision, row["reservation_id"]),
            )
            self._audit_locked(
                connection,
                reservation_id=str(row["reservation_id"]),
                participant_id=str(row["participant_id"]),
                action="expire",
                previous_state=str(row["state"]),
                next_state="EXPIRED",
                topology_revision=revision,
                now=now,
            )
        return revision

    @staticmethod
    def _row_public(row: sqlite3.Row, *, token: Optional[str] = None) -> dict[str, Any]:
        result = {
            key: row[key]
            for key in (
                "reservation_id",
                "participant_id",
                "model_name",
                "model_revision",
                "total_layers",
                "layer_capacity",
                "layer_start",
                "layer_end",
                "placement_mode",
                "state",
                "node_id",
                "peer_id",
                "rpc_uid",
                "created_at",
                "updated_at",
                "last_heartbeat_at",
                "expires_at",
                "topology_revision",
                "terminal_reason",
            )
        }
        result["created_at_iso"] = _utc(float(row["created_at"]))
        result["updated_at_iso"] = _utc(float(row["updated_at"]))
        result["expires_at_iso"] = _utc(float(row["expires_at"]))
        if token is not None:
            result["reservation_token"] = token
        return result

    @staticmethod
    def _request_document(request: ReservationRequest) -> dict[str, Any]:
        return {
            "participant_id": request.participant_id,
            "idempotency_key": request.idempotency_key,
            "model_name": request.model_name,
            "model_revision": request.model_revision,
            "layer_capacity": request.layer_capacity,
            "placement_mode": request.placement_mode,
            "layer_start": request.layer_start,
            "layer_end": request.layer_end,
        }

    @staticmethod
    def _reservation_nodes(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [
            {
                "peer_id": str(row["peer_id"] or f"reservation:{row['reservation_id']}"),
                "rpc_uid": str(row["rpc_uid"] or f"reservation:{row['reservation_id']}"),
                "node_id": str(row["node_id"] or row["reservation_id"]),
                "layer_start": int(row["layer_start"]),
                "layer_end": int(row["layer_end"]),
                "reservation_state": str(row["state"]),
            }
            for row in rows
        ]

    @staticmethod
    def _model_limits(model_name: str, layer_capacity: int) -> int:
        model = SUPPORTED_MODELS.get(model_name)
        if model is None:
            raise PlacementError("unsupported_model", f"Unsupported model {model_name!r}")
        total_layers = int(model["num_layers"])
        if not 1 <= layer_capacity <= total_layers:
            raise PlacementError(
                "invalid_layer_capacity",
                f"layer_capacity must be between 1 and {total_layers}",
            )
        return total_layers

    def _active_rows_locked(
        self,
        connection: sqlite3.Connection,
        model_name: str,
        model_revision: str,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM reservations
            WHERE model_name=? AND model_revision=?
              AND state IN ('RESERVED', 'JOINING', 'ONLINE')
            ORDER BY layer_start, layer_end, reservation_id
            """,
            (model_name, model_revision),
        ).fetchall()

    def reserve(self, request: ReservationRequest) -> dict[str, Any]:
        total_layers = self._model_limits(request.model_name, request.layer_capacity)
        if request.placement_mode == "custom" and int(request.layer_end or 0) > total_layers:
            raise PlacementError(
                "invalid_layer_range",
                f"layer_end exceeds model depth {total_layers}",
            )
        request_document = self._request_document(request)
        request_fingerprint = _fingerprint(request_document)
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_revision = self._expire_locked(connection, now)
            existing = connection.execute(
                """
                SELECT * FROM reservations
                WHERE participant_id=? AND idempotency_key=?
                """,
                (request.participant_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise PlacementConflict(
                        "idempotency_conflict",
                        "The idempotency key was already used for different placement inputs.",
                    )
                if existing["state"] in TERMINAL_STATES:
                    raise PlacementConflict(
                        "idempotency_terminal",
                        "The idempotent reservation is already terminal; submit a new key.",
                        details={"reservation": self._row_public(existing)},
                    )
                token = self._derive_token(
                    str(existing["reservation_id"]),
                    str(existing["participant_id"]),
                )
                connection.commit()
                return {
                    "idempotent_replay": True,
                    "topology_revision": current_revision,
                    "reservation": self._row_public(existing, token=token),
                }
            if (
                request.expected_topology_revision is not None
                and request.expected_topology_revision != current_revision
            ):
                raise PlacementConflict(
                    "topology_revision_stale",
                    "Placement topology changed; request a fresh plan before reserving.",
                    details={"topology_revision": current_revision},
                )

            active_rows = self._active_rows_locked(
                connection,
                request.model_name,
                request.model_revision,
            )
            active_nodes = self._reservation_nodes(active_rows)
            if request.placement_mode == "custom":
                layer_start = int(request.layer_start or 0)
                layer_end = int(request.layer_end or 0)
                conflict = next(
                    (
                        row
                        for row in active_rows
                        if ranges_overlap(
                            layer_start,
                            layer_end,
                            int(row["layer_start"]),
                            int(row["layer_end"]),
                        )
                    ),
                    None,
                )
                if conflict is not None:
                    raise PlacementConflict(
                        "placement_conflict",
                        "The requested custom range overlaps an active reservation.",
                        details={"conflict": self._row_public(conflict)},
                    )
            else:
                recommendation = recommend_exclusive_range(
                    active_nodes,
                    total_layers,
                    request.layer_capacity,
                )
                if recommendation is None:
                    raise PlacementConflict(
                        "capacity_unavailable",
                        "No non-overlapping range is available for this layer capacity.",
                        details={"topology_revision": current_revision},
                    )
                layer_start = int(recommendation["layer_start"])
                layer_end = int(recommendation["layer_end"])

            reservation_id = uuid4().hex
            token = self._derive_token(reservation_id, request.participant_id)
            revision = self._bump_revision_locked(connection)
            expires_at = now + self.startup_ttl_seconds
            connection.execute(
                """
                INSERT INTO reservations(
                    reservation_id, participant_id, idempotency_key,
                    request_fingerprint, token_hash, model_name, model_revision,
                    total_layers, layer_capacity, layer_start, layer_end,
                    placement_mode, state, created_at, updated_at, expires_at,
                    topology_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    request.participant_id,
                    request.idempotency_key,
                    request_fingerprint,
                    self._token_hash(token),
                    request.model_name,
                    request.model_revision,
                    total_layers,
                    request.layer_capacity,
                    layer_start,
                    layer_end,
                    request.placement_mode,
                    now,
                    now,
                    expires_at,
                    revision,
                ),
            )
            self._audit_locked(
                connection,
                reservation_id=reservation_id,
                participant_id=request.participant_id,
                action="reserve",
                previous_state=None,
                next_state="RESERVED",
                topology_revision=revision,
                now=now,
                details={
                    "model_name": request.model_name,
                    "model_revision": request.model_revision,
                    "layer_start": layer_start,
                    "layer_end": layer_end,
                    "placement_mode": request.placement_mode,
                },
            )
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("Placement reservation insert did not persist")
        return {
            "idempotent_replay": False,
            "topology_revision": revision,
            "reservation": self._row_public(row, token=token),
        }

    def _owned_row_locked(
        self,
        connection: sqlite3.Connection,
        reservation_id: str,
        participant_id: str,
        reservation_token: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise PlacementError(
                "reservation_not_found",
                "The placement reservation does not exist.",
                status_code=404,
            )
        if not hmac.compare_digest(str(row["participant_id"]), participant_id):
            raise PlacementError(
                "reservation_owner_mismatch",
                "The reservation belongs to another participant.",
                status_code=403,
            )
        if not hmac.compare_digest(
            str(row["token_hash"]),
            self._token_hash(reservation_token),
        ):
            raise PlacementError(
                "reservation_token_invalid",
                "The reservation token is invalid.",
                status_code=403,
            )
        return row

    def mark_joining(
        self,
        reservation_id: str,
        mutation: JoiningMutation,
    ) -> dict[str, Any]:
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locked(connection, now)
            row = self._owned_row_locked(
                connection,
                reservation_id,
                mutation.participant_id,
                mutation.reservation_token,
            )
            if row["state"] == "JOINING":
                if row["node_id"] != mutation.node_id:
                    raise PlacementConflict(
                        "joining_identity_conflict",
                        "The reservation is already joining with another node ID.",
                    )
                connection.commit()
                return {
                    "idempotent_replay": True,
                    "topology_revision": self._revision_locked(connection),
                    "reservation": self._row_public(row),
                }
            if row["state"] != "RESERVED":
                raise PlacementConflict(
                    "invalid_state_transition",
                    f"Cannot mark reservation {row['state']} as JOINING.",
                )
            revision = self._bump_revision_locked(connection)
            connection.execute(
                """
                UPDATE reservations
                SET state='JOINING', node_id=?, updated_at=?, expires_at=?,
                    topology_revision=?
                WHERE reservation_id=?
                """,
                (
                    mutation.node_id,
                    now,
                    now + self.startup_ttl_seconds,
                    revision,
                    reservation_id,
                ),
            )
            self._audit_locked(
                connection,
                reservation_id=reservation_id,
                participant_id=mutation.participant_id,
                action="joining",
                previous_state="RESERVED",
                next_state="JOINING",
                topology_revision=revision,
                now=now,
                details={"node_id": mutation.node_id},
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            connection.commit()
        if updated is None:
            raise RuntimeError("JOINING placement mutation did not persist")
        return {
            "idempotent_replay": False,
            "topology_revision": revision,
            "reservation": self._row_public(updated),
        }

    def mark_online(
        self,
        reservation_id: str,
        mutation: OnlineMutation,
    ) -> dict[str, Any]:
        if not mutation.rpc_ready or not mutation.publication_ready:
            raise PlacementError(
                "provider_not_ready",
                "ONLINE requires both RPC readiness and a healthy advertisement.",
            )
        if mutation.peer_id != mutation.advertised_peer_id:
            raise PlacementError(
                "advertised_peer_mismatch",
                "The advertised peer does not match the started provider.",
            )
        if mutation.rpc_uid != mutation.advertised_rpc_uid:
            raise PlacementError(
                "advertised_rpc_mismatch",
                "The advertised RPC UID does not match the started provider.",
            )
        if mutation.model_revision != mutation.advertised_model_revision:
            raise PlacementError(
                "advertised_model_revision_mismatch",
                "The advertised model revision does not match the started provider.",
            )
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locked(connection, now)
            row = self._owned_row_locked(
                connection,
                reservation_id,
                mutation.participant_id,
                mutation.reservation_token,
            )
            if mutation.model_revision != str(row["model_revision"]):
                raise PlacementConflict(
                    "reservation_model_revision_mismatch",
                    "The provider model revision differs from its placement reservation.",
                )
            if row["state"] == "ONLINE":
                if any(
                    str(row[field] or "") != expected
                    for field, expected in (
                        ("node_id", mutation.node_id),
                        ("peer_id", mutation.peer_id),
                        ("rpc_uid", mutation.rpc_uid),
                    )
                ):
                    raise PlacementConflict(
                        "online_identity_conflict",
                        "The reservation is already ONLINE with different exact ownership.",
                    )
                connection.execute(
                    "UPDATE reservations SET last_heartbeat_at=?, updated_at=?, expires_at=? "
                    "WHERE reservation_id=?",
                    (now, now, now + self.online_ttl_seconds, reservation_id),
                )
                updated = connection.execute(
                    "SELECT * FROM reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                connection.commit()
                if updated is None:
                    raise RuntimeError("ONLINE placement renewal did not persist")
                return {
                    "idempotent_replay": True,
                    "topology_revision": self._revision_locked(connection),
                    "reservation": self._row_public(updated),
                }
            if row["state"] != "JOINING":
                raise PlacementConflict(
                    "invalid_state_transition",
                    f"Cannot mark reservation {row['state']} as ONLINE.",
                )
            if str(row["node_id"] or "") != mutation.node_id:
                raise PlacementConflict(
                    "joining_identity_conflict",
                    "ONLINE node ID differs from the JOINING owner.",
                )
            revision = self._bump_revision_locked(connection)
            connection.execute(
                """
                UPDATE reservations
                SET state='ONLINE', peer_id=?, rpc_uid=?, updated_at=?,
                    last_heartbeat_at=?, expires_at=?, topology_revision=?
                WHERE reservation_id=?
                """,
                (
                    mutation.peer_id,
                    mutation.rpc_uid,
                    now,
                    now,
                    now + self.online_ttl_seconds,
                    revision,
                    reservation_id,
                ),
            )
            self._audit_locked(
                connection,
                reservation_id=reservation_id,
                participant_id=mutation.participant_id,
                action="online",
                previous_state="JOINING",
                next_state="ONLINE",
                topology_revision=revision,
                now=now,
                details={
                    "node_id": mutation.node_id,
                    "peer_id": mutation.peer_id,
                    "rpc_uid": mutation.rpc_uid,
                    "rpc_ready": True,
                    "publication_ready": True,
                },
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            connection.commit()
        if updated is None:
            raise RuntimeError("ONLINE placement mutation did not persist")
        return {
            "idempotent_replay": False,
            "topology_revision": revision,
            "reservation": self._row_public(updated),
        }

    def renew(
        self,
        reservation_id: str,
        mutation: RenewMutation,
    ) -> dict[str, Any]:
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locked(connection, now)
            row = self._owned_row_locked(
                connection,
                reservation_id,
                mutation.participant_id,
                mutation.reservation_token,
            )
            state = str(row["state"])
            if state not in ACTIVE_STATES:
                raise PlacementConflict(
                    "reservation_not_active",
                    f"Cannot renew a {state} reservation.",
                )
            if state == "ONLINE":
                if mutation.peer_id != row["peer_id"] or mutation.rpc_uid != row["rpc_uid"]:
                    raise PlacementConflict(
                        "online_identity_conflict",
                        "ONLINE renewal must preserve the exact peer and RPC UID.",
                    )
                ttl = self.online_ttl_seconds
            else:
                ttl = self.startup_ttl_seconds
            connection.execute(
                """
                UPDATE reservations
                SET updated_at=?, last_heartbeat_at=?, expires_at=?
                WHERE reservation_id=?
                """,
                (now, now, now + ttl, reservation_id),
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            revision = self._revision_locked(connection)
            self._audit_locked(
                connection,
                reservation_id=reservation_id,
                participant_id=mutation.participant_id,
                action="renew",
                previous_state=state,
                next_state=state,
                topology_revision=revision,
                now=now,
            )
            connection.commit()
        if updated is None:
            raise RuntimeError("Placement heartbeat did not persist")
        return {
            "idempotent_replay": False,
            "topology_revision": revision,
            "reservation": self._row_public(updated),
        }

    def release(
        self,
        reservation_id: str,
        mutation: ReleaseMutation,
    ) -> dict[str, Any]:
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locked(connection, now)
            row = self._owned_row_locked(
                connection,
                reservation_id,
                mutation.participant_id,
                mutation.reservation_token,
            )
            state = str(row["state"])
            if state in TERMINAL_STATES:
                connection.commit()
                return {
                    "idempotent_replay": True,
                    "topology_revision": self._revision_locked(connection),
                    "reservation": self._row_public(row),
                }
            revision = self._bump_revision_locked(connection)
            connection.execute(
                """
                UPDATE reservations
                SET state='OFFLINE', updated_at=?, topology_revision=?, terminal_reason=?
                WHERE reservation_id=?
                """,
                (now, revision, mutation.reason, reservation_id),
            )
            self._audit_locked(
                connection,
                reservation_id=reservation_id,
                participant_id=mutation.participant_id,
                action="release",
                previous_state=state,
                next_state="OFFLINE",
                topology_revision=revision,
                now=now,
                details={"reason": mutation.reason},
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            connection.commit()
        if updated is None:
            raise RuntimeError("Placement release did not persist")
        return {
            "idempotent_replay": False,
            "topology_revision": revision,
            "reservation": self._row_public(updated),
        }

    def plan(
        self,
        model_name: str,
        model_revision: str,
        layer_capacity: int,
    ) -> dict[str, Any]:
        total_layers = self._model_limits(model_name, layer_capacity)
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = self._expire_locked(connection, now)
            active_rows = self._active_rows_locked(
                connection,
                model_name,
                model_revision,
            )
            online_rows = [row for row in active_rows if row["state"] == "ONLINE"]
            active_nodes = self._reservation_nodes(active_rows)
            online_nodes = self._reservation_nodes(online_rows)
            recommendation = recommend_exclusive_range(
                active_nodes,
                total_layers,
                layer_capacity,
            )
            occupancy_plan = build_serving_plan(
                active_nodes,
                total_layers,
                layer_capacity,
            )
            online_plan = build_serving_plan(
                online_nodes,
                total_layers,
                layer_capacity,
            )
            connection.commit()
        public_recommendation = None
        if recommendation is not None:
            public_recommendation = {
                key: value
                for key, value in recommendation.items()
                if key not in {"projected_nodes", "provider_count_sum", "provider_count_max"}
            }
            public_recommendation["extends_reachable_prefix"] = (
                int(recommendation["reachable_prefix"])
                > int(occupancy_plan["reachable_prefix"])
            )
        return {
            "schema_version": 1,
            "model_name": model_name,
            "model_revision": model_revision,
            "total_layers": total_layers,
            "requested_layer_count": layer_capacity,
            "topology_revision": revision,
            "captured_at": _utc(now),
            "capacity_available": public_recommendation is not None,
            "recommendation": public_recommendation,
            "occupancy_plan": occupancy_plan,
            "online_plan": online_plan,
            "reservations": [self._row_public(row) for row in active_rows],
        }

    def participant_reservations(self, participant_id: str) -> dict[str, Any]:
        normalized = participant_id.strip()
        if not normalized:
            raise PlacementError("participant_invalid", "participant_id must not be blank")
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = self._expire_locked(connection, now)
            rows = connection.execute(
                "SELECT * FROM reservations WHERE participant_id=? ORDER BY created_at",
                (normalized,),
            ).fetchall()
            connection.commit()
        return {
            "participant_id": normalized,
            "topology_revision": revision,
            "reservations": [self._row_public(row) for row in rows],
        }

    def audit(self, limit: int = 100) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM placement_audit ORDER BY event_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            revision = self._revision_locked(connection)
        return {
            "topology_revision": revision,
            "events": [
                {
                    **{
                        key: value
                        for key, value in dict(row).items()
                        if key != "details_json"
                    },
                    "details": json.loads(row["details_json"]),
                    "observed_at_iso": _utc(float(row["observed_at"])),
                }
                for row in rows
            ],
        }


def create_placement_app(
    database_path: str | Path,
    *,
    auth_token: str,
    token_secret: Optional[str] = None,
    startup_ttl_seconds: float = 90.0,
    online_ttl_seconds: float = 300.0,
    clock: Callable[[], float] = time.time,
    deployment_commit: str | None = None,
    failure_domain: str | None = None,
) -> FastAPI:
    if len(auth_token) < 32:
        raise ValueError("placement auth_token must contain at least 32 characters")
    store = PlacementStore(
        database_path,
        token_secret=token_secret or auth_token,
        startup_ttl_seconds=startup_ttl_seconds,
        online_ttl_seconds=online_ttl_seconds,
        clock=clock,
    )
    app = FastAPI(title="DistribLLM Swarm Placement", version="1.0.0")
    app.state.store = store

    def authorize(authorization: Optional[str]) -> None:
        scheme, separator, credential = (authorization or "").partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(credential, auth_token)
        ):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "placement_authentication_failed",
                    "message": "A valid placement service bearer token is required.",
                },
            )

    def placement_call(target: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return target()
        except PlacementError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.response()) from exc

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "component_role": "coordinator",
            "service_protocol_version": 1,
            "schema_version": SCHEMA_VERSION,
            "deployment_commit": deployment_commit,
            "failure_domain": failure_domain,
            "topology_revision": store.audit(limit=1)["topology_revision"],
        }

    @app.post("/v1/reservations")
    async def reserve(
        request: ReservationRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.reserve(request))

    @app.post("/v1/reservations/{reservation_id}/joining")
    async def joining(
        reservation_id: str,
        mutation: JoiningMutation,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.mark_joining(reservation_id, mutation))

    @app.post("/v1/reservations/{reservation_id}/online")
    async def online(
        reservation_id: str,
        mutation: OnlineMutation,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.mark_online(reservation_id, mutation))

    @app.post("/v1/reservations/{reservation_id}/renew")
    async def renew(
        reservation_id: str,
        mutation: RenewMutation,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.renew(reservation_id, mutation))

    @app.post("/v1/reservations/{reservation_id}/release")
    async def release(
        reservation_id: str,
        mutation: ReleaseMutation,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.release(reservation_id, mutation))

    @app.get("/v1/models/{model_name:path}/plan")
    async def plan(
        model_name: str,
        model_revision: str = Query(..., min_length=1),
        layer_capacity: int = Query(..., ge=1),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(
            lambda: store.plan(model_name, model_revision.strip(), layer_capacity)
        )

    @app.get("/v1/participants/{participant_id}/reservations")
    async def participant_reservations(
        participant_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return placement_call(lambda: store.participant_reservations(participant_id))

    @app.get("/v1/audit")
    async def audit(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return store.audit(limit)

    return app


def create_default_placement_app() -> FastAPI:
    auth_token = os.environ.get("DISTRIBLLM_PLACEMENT_AUTH_TOKEN", "").strip()
    if not auth_token:
        raise RuntimeError("DISTRIBLLM_PLACEMENT_AUTH_TOKEN is required")
    database_path = Path(
        os.environ.get(
            "DISTRIBLLM_PLACEMENT_DB",
            str(Path.home() / ".distribllm" / "placement.sqlite3"),
        )
    )
    return create_placement_app(
        database_path,
        auth_token=auth_token,
        token_secret=os.environ.get("DISTRIBLLM_PLACEMENT_TOKEN_SECRET", "").strip()
        or auth_token,
        startup_ttl_seconds=float(
            os.environ.get("DISTRIBLLM_PLACEMENT_STARTUP_TTL_SECONDS", "90")
        ),
        online_ttl_seconds=float(
            os.environ.get("DISTRIBLLM_PLACEMENT_ONLINE_TTL_SECONDS", "300")
        ),
        deployment_commit=os.environ.get("DISTRIBLLM_DEPLOY_COMMIT") or None,
        failure_domain=os.environ.get("DISTRIBLLM_FAILURE_DOMAIN") or None,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "placement.service:create_default_placement_app",
        factory=True,
        host=os.environ.get("DISTRIBLLM_PLACEMENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("DISTRIBLLM_PLACEMENT_PORT", "7200")),
    )
