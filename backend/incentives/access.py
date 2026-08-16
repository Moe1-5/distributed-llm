"""Credit-gated developer API keys, reservations, and signed capabilities."""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, cast
from uuid import uuid4

from blake3 import blake3

from constants import SUPPORTED_MODELS
from incentives.identity import ApplicationIdentity, load_application_identity
from incentives.protocol import ProtocolError, verify_signed_document

AccessMode = Literal["off", "shadow", "enforced"]


class AccessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_access_mode(value: str) -> AccessMode:
    normalized = value.strip().lower()
    if normalized not in {"off", "shadow", "enforced"}:
        raise ValueError("DISTRIBLLM_API_ACCESS_MODE must be off, shadow, or enforced")
    return cast(AccessMode, normalized)


@dataclass(frozen=True)
class AccessConfig:
    mode: AccessMode
    database_path: Path
    price_scale: int
    capability_ttl_seconds: int


def get_access_config() -> AccessConfig:
    price_scale = int(os.environ.get("DISTRIBLLM_API_PRICE_SCALE", "1"))
    ttl = int(os.environ.get("DISTRIBLLM_API_CAPABILITY_TTL", "120"))
    if price_scale <= 0 or ttl <= 0:
        raise ValueError("API price scale and capability TTL must be positive")
    database = Path(
        os.environ.get(
            "DISTRIBLLM_API_ACCESS_DB",
            str(Path.home() / ".distribllm" / "api-access.sqlite3"),
        )
    ).expanduser().resolve()
    return AccessConfig(
        mode=parse_access_mode(os.environ.get("DISTRIBLLM_API_ACCESS_MODE", "off")),
        database_path=database,
        price_scale=price_scale,
        capability_ttl_seconds=ttl,
    )


def _key_hash(value: str) -> str:
    return blake3(value.encode("utf-8")).hexdigest()


def _model_weight(model_name: str) -> int:
    model = SUPPORTED_MODELS.get(model_name)
    if model is None:
        raise AccessError("unsupported_model", f"Unsupported model: {model_name}")
    return max(1, int(model["hidden_size"]) // 768)


class ApiAccessStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.initialize()

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._lock, self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    owner_public_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    request_id TEXT PRIMARY KEY,
                    key_id TEXT NOT NULL REFERENCES api_keys(key_id),
                    model_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    reserved_units INTEGER NOT NULL,
                    charged_units INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS capability_nonces (
                    nonce TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS api_usage_key_status
                    ON api_usage(key_id, status);
                """
            )
        self._secure_files()

    def _secure_files(self) -> None:
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                os.chmod(candidate, 0o600)

    def create_key(
        self,
        owner_public_key: str,
        name: str,
        *,
        verified_credits: int,
    ) -> dict[str, Any]:
        if verified_credits <= 0:
            raise AccessError(
                "verified_credits_required",
                "A positive verified useful-work credit balance is required to create an API key.",
            )
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 80:
            raise AccessError("invalid_key_name", "API key name must contain 1 to 80 characters.")
        key_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        token = f"dllm_{key_id[:12]}_{secret}"
        now = int(time.time())
        with self._lock, self.connection() as connection:
            connection.execute(
                "INSERT INTO api_keys(key_id, owner_public_key, name, key_prefix, key_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key_id, owner_public_key, normalized_name, token[:22], _key_hash(token), now),
            )
        self._secure_files()
        return {
            "key_id": key_id,
            "name": normalized_name,
            "key_prefix": token[:22],
            "api_key": token,
            "created_at": now,
        }

    def list_keys(self, owner_public_key: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT key_id, name, key_prefix, created_at, revoked_at "
                "FROM api_keys WHERE owner_public_key = ? ORDER BY created_at DESC",
                (owner_public_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_key(self, owner_public_key: str, key_id: str) -> bool:
        with self._lock, self.connection() as connection:
            cursor = connection.execute(
                "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) "
                "WHERE key_id = ? AND owner_public_key = ?",
                (int(time.time()), key_id, owner_public_key),
            )
        return cursor.rowcount == 1

    def authenticate(self, token: str) -> dict[str, Any]:
        if not token.startswith("dllm_") or len(token) < 40:
            raise AccessError("invalid_api_key", "API key is invalid.")
        with self.connection() as connection:
            row = connection.execute(
                "SELECT key_id, owner_public_key, name, key_prefix, created_at, revoked_at "
                "FROM api_keys WHERE key_hash = ?",
                (_key_hash(token),),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AccessError("invalid_api_key", "API key is invalid or revoked.")
        return dict(row)

    def reserve(
        self,
        *,
        key_id: str,
        model_name: str,
        estimated_positions: int,
        verified_credits: int,
        mode: AccessMode,
        price_scale: int,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if mode == "off":
            raise AccessError("developer_api_disabled", "Developer API access is disabled.")
        if estimated_positions <= 0:
            raise AccessError("invalid_position_count", "Estimated positions must be positive.")
        request_id = request_id or str(uuid4())
        reserved_units = estimated_positions * _model_weight(model_name) * price_scale
        now = int(time.time())
        try:
            with self._lock, self.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                key = connection.execute(
                    "SELECT owner_public_key, revoked_at FROM api_keys WHERE key_id = ?",
                    (key_id,),
                ).fetchone()
                if key is None or key["revoked_at"] is not None:
                    raise AccessError("invalid_api_key", "API key is invalid or revoked.")
                used = connection.execute(
                    "SELECT COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_units "
                    "ELSE charged_units END), 0) AS units FROM api_usage AS usage "
                    "JOIN api_keys AS keys ON keys.key_id = usage.key_id "
                    "WHERE keys.owner_public_key = ? "
                    "AND status IN ('reserved', 'completed')",
                    (key["owner_public_key"],),
                ).fetchone()
                available = max(0, int(verified_credits) - int(used["units"]))
                if mode == "enforced" and available < reserved_units:
                    raise AccessError(
                        "insufficient_credits",
                        f"This request needs {reserved_units} credits; {available} are available.",
                    )
                connection.execute(
                    "INSERT INTO api_usage(request_id, key_id, model_name, mode, "
                    "reserved_units, charged_units, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, 'reserved', ?)",
                    (request_id, key_id, model_name, mode, reserved_units, now),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise AccessError("duplicate_request", "API request ID was already used.") from exc
        return {
            "request_id": request_id,
            "reserved_units": reserved_units,
            "mode": mode,
        }

    def complete(
        self,
        request_id: str,
        *,
        actual_positions: int,
        price_scale: int,
    ) -> dict[str, Any]:
        with self._lock, self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT model_name, mode, reserved_units, charged_units, status "
                "FROM api_usage WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise AccessError("reservation_not_found", "API reservation was not found.")
            if row["status"] == "completed":
                return {
                    "request_id": request_id,
                    "charged_units": int(row["charged_units"]),
                    "status": "completed",
                }
            if row["status"] != "reserved":
                raise AccessError("reservation_released", "API reservation is no longer active.")
            actual_units = actual_positions * _model_weight(row["model_name"]) * price_scale
            if actual_units > int(row["reserved_units"]):
                raise AccessError("reservation_exceeded", "Actual usage exceeded its reservation.")
            charged_units = actual_units if row["mode"] == "enforced" else 0
            connection.execute(
                "UPDATE api_usage SET status = 'completed', charged_units = ?, completed_at = ? "
                "WHERE request_id = ?",
                (charged_units, int(time.time()), request_id),
            )
            connection.commit()
        return {
            "request_id": request_id,
            "charged_units": charged_units,
            "projected_units": actual_units,
            "status": "completed",
        }

    def release(self, request_id: str) -> bool:
        with self._lock, self.connection() as connection:
            cursor = connection.execute(
                "UPDATE api_usage SET status = 'released', charged_units = 0, completed_at = ? "
                "WHERE request_id = ? AND status = 'reserved'",
                (int(time.time()), request_id),
            )
        return cursor.rowcount == 1

    def register_capability(self, nonce: str, request_id: str, expires_at: int) -> None:
        with self._lock, self.connection() as connection:
            connection.execute(
                "INSERT INTO capability_nonces(nonce, request_id, expires_at) VALUES (?, ?, ?)",
                (nonce, request_id, expires_at),
            )

    def consume_capability(self, nonce: str, request_id: str, now: int) -> None:
        with self._lock, self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_id, expires_at, consumed_at FROM capability_nonces WHERE nonce = ?",
                (nonce,),
            ).fetchone()
            if row is None or row["request_id"] != request_id:
                raise AccessError("invalid_capability", "Capability is not registered.")
            if row["consumed_at"] is not None:
                raise AccessError("capability_replayed", "Capability was already consumed.")
            if int(row["expires_at"]) < now:
                raise AccessError("capability_expired", "Capability has expired.")
            connection.execute(
                "UPDATE capability_nonces SET consumed_at = ? WHERE nonce = ?",
                (now, nonce),
            )
            connection.commit()

    def usage_summary(self, key_id: str) -> dict[str, int]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(charged_units), 0) AS spent, "
                "COALESCE(SUM(CASE WHEN status = 'reserved' THEN reserved_units ELSE 0 END), 0) "
                "AS reserved FROM api_usage WHERE key_id = ?",
                (key_id,),
            ).fetchone()
        return {"spent_units": int(row["spent"]), "reserved_units": int(row["reserved"])}

    def owner_usage_summary(self, owner_public_key: str) -> dict[str, int]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(usage.charged_units), 0) AS spent, "
                "COALESCE(SUM(CASE WHEN usage.status = 'reserved' "
                "THEN usage.reserved_units ELSE 0 END), 0) AS reserved "
                "FROM api_usage AS usage JOIN api_keys AS keys "
                "ON keys.key_id = usage.key_id WHERE keys.owner_public_key = ?",
                (owner_public_key,),
            ).fetchone()
        return {"spent_units": int(row["spent"]), "reserved_units": int(row["reserved"])}


class ApiAccessManager:
    def __init__(
        self,
        config: Optional[AccessConfig] = None,
        store: Optional[ApiAccessStore] = None,
        identity: Optional[ApplicationIdentity] = None,
    ) -> None:
        self.config = config or get_access_config()
        self.store = store or ApiAccessStore(self.config.database_path)
        self.identity = identity or load_application_identity()

    def issue_capability(
        self,
        *,
        request_id: str,
        key_id: str,
        model_name: str,
        max_positions: int,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        issued_at = int(time.time()) if now is None else int(now)
        expires_at = issued_at + self.config.capability_ttl_seconds
        nonce = secrets.token_hex(16)
        document = self.identity.sign(
            {
                "document_type": "api_inference_capability",
                "request_id": request_id,
                "api_key_id": key_id,
                "model_name": model_name,
                "max_positions": max_positions,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "nonce": nonce,
            }
        )
        self.store.register_capability(nonce, request_id, expires_at)
        return document

    def verify_and_consume_capability(
        self,
        document: dict[str, Any],
        *,
        request_id: str,
        model_name: str,
        positions: int,
        now: Optional[int] = None,
    ) -> dict[str, Any]:
        try:
            payload = verify_signed_document(
                document,
                expected_type="api_inference_capability",
            )
        except ProtocolError as exc:
            raise AccessError("invalid_capability", str(exc)) from exc
        current_time = int(time.time()) if now is None else int(now)
        if document.get("public_key") != self.identity.public_key:
            raise AccessError("invalid_capability", "Capability signer is not trusted.")
        if payload.get("request_id") != request_id or payload.get("model_name") != model_name:
            raise AccessError("invalid_capability", "Capability request or model does not match.")
        if (
            isinstance(payload.get("max_positions"), bool)
            or not isinstance(payload.get("max_positions"), int)
            or positions > payload["max_positions"]
        ):
            raise AccessError("capability_oversized", "Request exceeds the capability position limit.")
        issued_at = payload.get("issued_at")
        expires_at = payload.get("expires_at")
        nonce = payload.get("nonce")
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (issued_at, expires_at)
        ):
            raise AccessError("invalid_capability", "Capability timestamps are invalid.")
        if not isinstance(nonce, str) or not nonce:
            raise AccessError("invalid_capability", "Capability nonce is invalid.")
        if current_time < issued_at or current_time > expires_at:
            raise AccessError("capability_expired", "Capability has expired.")
        self.store.consume_capability(nonce, request_id, current_time)
        return payload


_manager: Optional[ApiAccessManager] = None
_manager_lock = threading.Lock()


def get_api_access_manager() -> ApiAccessManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ApiAccessManager()
        return _manager
