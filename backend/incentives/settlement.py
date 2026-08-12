"""FastAPI settlement service with an append-only SQLite useful-work ledger."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from constants import SUPPORTED_MODELS
from incentives.config import IncentivesMode, parse_incentives_mode
from incentives.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    hash_document,
    normalize_route,
    route_id,
    validate_commitment,
    validate_position_count,
    verify_signed_document,
)

SettlementMode = IncentivesMode
SCHEMA_VERSION = 1


def default_policy() -> dict[str, Any]:
    return {
        "reward_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "reward_scale": 1,
        "model_compute_weights": {
            model_id: max(1, int(info["hidden_size"]) // 768)
            for model_id, info in SUPPORTED_MODELS.items()
        },
        "model_revisions": {model_id: ["main"] for model_id in SUPPORTED_MODELS},
        "model_layer_counts": {
            model_id: int(info["num_layers"])
            for model_id, info in SUPPORTED_MODELS.items()
        },
    }


@dataclass(frozen=True)
class VerifiedReceipt:
    request_id: str
    receipt_hash: str
    worker_nonce: str
    worker_public_key: str
    worker_peer_id: str
    generator_public_key: str
    generator_peer_id: str
    model_name: str
    model_revision: str
    route_id: str
    session_id: str
    layer_start: int
    layer_end: int
    position_count: int
    reward_units: int
    reward_version: int
    submission: dict[str, Any]


class ReceiptSubmission(BaseModel):
    worker_receipt: dict[str, Any]
    generator_acceptance: dict[str, Any]
    worker_presence: dict[str, Any]
    generator_presence: dict[str, Any]
    route: list[dict[str, Any]]


def _require_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


def _require_timestamp(payload: dict[str, Any], field: str = "timestamp") -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{field} must be an integer timestamp")
    return value


def _validate_fresh(timestamp: int, now: int, window_seconds: int, label: str) -> None:
    if abs(now - timestamp) > window_seconds:
        raise ProtocolError(f"{label} timestamp is outside the allowed window")


def _validate_presence(
    document: dict[str, Any],
    *,
    expected_public_key: str,
    expected_peer_id: str,
    now: int,
    window_seconds: int,
) -> dict[str, Any]:
    payload = verify_signed_document(document, expected_type="presence")
    if document["public_key"] != expected_public_key:
        raise ProtocolError("Presence application key does not match receipt identity")
    if payload.get("application_public_key") != expected_public_key:
        raise ProtocolError("Presence payload application key is inconsistent")
    if payload.get("p2p_peer_id") != expected_peer_id:
        raise ProtocolError("Presence p2p peer ID does not match receipt identity")
    _require_text(payload, "nonce")
    _validate_fresh(
        _require_timestamp(payload),
        now,
        window_seconds,
        "Presence",
    )
    return payload


def validate_submission(
    submission: dict[str, Any],
    policy: dict[str, Any],
    *,
    now: int | None = None,
    window_seconds: int = 300,
) -> VerifiedReceipt:
    now = int(time.time()) if now is None else int(now)
    worker_document = submission.get("worker_receipt")
    acceptance_document = submission.get("generator_acceptance")
    if not isinstance(worker_document, dict) or not isinstance(acceptance_document, dict):
        raise ProtocolError("Submission must contain signed worker and generator documents")

    worker = verify_signed_document(worker_document, expected_type="worker_receipt")
    acceptance = verify_signed_document(
        acceptance_document,
        expected_type="generator_acceptance",
    )
    worker_key = str(worker_document["public_key"])
    generator_key = str(acceptance_document["public_key"])
    if worker_key == generator_key:
        raise ProtocolError("Generator and worker identities must be distinct")

    request_id = _require_text(worker, "request_id")
    session_id = _require_text(worker, "session_id")
    claimed_route_id = _require_text(worker, "route_id")
    worker_peer_id = _require_text(worker, "worker_peer_id")
    generator_peer_id = _require_text(worker, "generator_peer_id")
    if worker_peer_id == generator_peer_id:
        raise ProtocolError("Generator and worker p2p peers must be distinct")
    model_name = _require_text(worker, "model_name")
    model_revision = _require_text(worker, "model_revision")
    rpc_uid = _require_text(worker, "rpc_uid")
    worker_nonce = _require_text(worker, "nonce")
    validate_commitment(worker.get("input_commitment"), "input_commitment")
    validate_commitment(worker.get("response_commitment"), "response_commitment")
    position_count = validate_position_count(worker.get("position_count"))
    worker_timestamp = _require_timestamp(worker)
    _validate_fresh(worker_timestamp, now, window_seconds, "Worker receipt")

    if worker.get("worker_public_key") != worker_key:
        raise ProtocolError("Worker payload key does not match its signature")
    if worker.get("generator_public_key") != generator_key:
        raise ProtocolError("Worker receipt generator key does not match acceptance")
    if acceptance.get("generator_public_key") != generator_key:
        raise ProtocolError("Acceptance payload key does not match its signature")
    if acceptance.get("worker_public_key") != worker_key:
        raise ProtocolError("Acceptance worker key does not match receipt")
    if acceptance.get("accepted") is not True:
        raise ProtocolError("Generator did not accept the worker result")

    signed_route = normalize_route(worker.get("selected_route"))
    if route_id(signed_route) != claimed_route_id:
        raise ProtocolError("Worker receipt selected route does not match its route ID")

    receipt_hash = hash_document(worker_document)
    paired_fields = ("request_id", "session_id", "route_id")
    for field in paired_fields:
        if acceptance.get(field) != worker.get(field):
            raise ProtocolError(f"Generator acceptance {field} does not match worker receipt")
    if acceptance.get("worker_receipt_hash") != receipt_hash:
        raise ProtocolError("Generator acceptance does not commit to the worker receipt")
    if acceptance.get("generator_peer_id") != generator_peer_id:
        raise ProtocolError("Generator peer ID is inconsistent across the receipt pair")
    _validate_fresh(
        _require_timestamp(acceptance),
        now,
        window_seconds,
        "Generator acceptance",
    )

    worker_presence = submission.get("worker_presence")
    generator_presence = submission.get("generator_presence")
    if not isinstance(worker_presence, dict) or not isinstance(generator_presence, dict):
        raise ProtocolError("Submission must include signed worker and generator presence")
    _validate_presence(
        worker_presence,
        expected_public_key=worker_key,
        expected_peer_id=worker_peer_id,
        now=now,
        window_seconds=window_seconds,
    )
    _validate_presence(
        generator_presence,
        expected_public_key=generator_key,
        expected_peer_id=generator_peer_id,
        now=now,
        window_seconds=window_seconds,
    )

    route = normalize_route(submission.get("route"))
    if route != signed_route:
        raise ProtocolError("Submitted route does not match the worker-signed route")
    if route_id(route) != claimed_route_id:
        raise ProtocolError("Selected route does not match the signed route ID")
    try:
        layer_start = worker["layer_start"]
        layer_end = worker["layer_end"]
    except KeyError as exc:
        raise ProtocolError("Worker receipt has invalid layer bounds") from exc
    if (
        isinstance(layer_start, bool)
        or isinstance(layer_end, bool)
        or not isinstance(layer_start, int)
        or not isinstance(layer_end, int)
    ):
        raise ProtocolError("Worker receipt layer bounds must be integers")
    matching_members = [
        member
        for member in route
        if member["peer_id"] == worker_peer_id
        and member["application_public_key"] == worker_key
        and member["rpc_uid"] == rpc_uid
        and member["layer_start"] == layer_start
        and member["layer_end"] == layer_end
    ]
    if len(matching_members) != 1:
        raise ProtocolError("Worker receipt is not a unique member of the selected route")

    model_weights = policy.get("model_compute_weights", {})
    revisions = policy.get("model_revisions", {})
    layer_counts = policy.get("model_layer_counts", {})
    if model_name not in model_weights or model_name not in layer_counts:
        raise ProtocolError("Model is not allowed by the active reward policy")
    if model_revision not in revisions.get(model_name, []):
        raise ProtocolError("Model revision is not allowed by the active reward policy")
    total_layers = int(layer_counts[model_name])
    if not 0 <= layer_start < layer_end <= total_layers:
        raise ProtocolError("Served layer range is outside the model bounds")
    if route[-1]["layer_end"] != total_layers:
        raise ProtocolError("Selected route does not cover the complete model")

    reward_scale = int(policy["reward_scale"])
    model_weight = int(model_weights[model_name])
    if reward_scale <= 0 or model_weight <= 0:
        raise ProtocolError("Reward policy weights must be positive integers")
    reward_units = (
        position_count
        * (layer_end - layer_start)
        * model_weight
        * reward_scale
    )
    return VerifiedReceipt(
        request_id=request_id,
        receipt_hash=receipt_hash,
        worker_nonce=worker_nonce,
        worker_public_key=worker_key,
        worker_peer_id=worker_peer_id,
        generator_public_key=generator_key,
        generator_peer_id=generator_peer_id,
        model_name=model_name,
        model_revision=model_revision,
        route_id=claimed_route_id,
        session_id=session_id,
        layer_start=layer_start,
        layer_end=layer_end,
        position_count=position_count,
        reward_units=reward_units,
        reward_version=int(policy["reward_version"]),
        submission=submission,
    )


class SettlementStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()
            self._secure_files()

    def _secure_files(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                try:
                    os.chmod(candidate, 0o600)
                except FileNotFoundError:
                    pass

    def initialize(self) -> None:
        with self.connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, SCHEMA_VERSION}:
                raise RuntimeError(
                    f"Unsupported settlement schema version {version}; "
                    "configure a compatible database or run an approved migration"
                )
            existing_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if version == 0 and "identities" in existing_tables:
                identity_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(identities)")
                }
                binding_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(presence_bindings)"
                    )
                }
                if identity_columns != {"public_key", "first_seen_at"} or not {
                    "public_key",
                    "p2p_peer_id",
                    "first_seen_at",
                }.issubset(binding_columns):
                    raise RuntimeError(
                        "Settlement database uses an incompatible pre-release schema; "
                        "back it up and configure a new DISTRIBLLM_SETTLEMENT_DB path"
                    )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_versions (
                    reward_version INTEGER PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identities (
                    public_key TEXT PRIMARY KEY,
                    first_seen_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presence_bindings (
                    public_key TEXT NOT NULL REFERENCES identities(public_key),
                    p2p_peer_id TEXT NOT NULL UNIQUE,
                    first_seen_at INTEGER NOT NULL,
                    PRIMARY KEY(public_key, p2p_peer_id)
                );
                CREATE TABLE IF NOT EXISTS presence_records (
                    presence_hash TEXT PRIMARY KEY,
                    public_key TEXT NOT NULL,
                    p2p_peer_id TEXT NOT NULL,
                    presence_json TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    FOREIGN KEY(public_key, p2p_peer_id)
                        REFERENCES presence_bindings(public_key, p2p_peer_id)
                );
                CREATE TABLE IF NOT EXISTS receipt_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    worker_nonce TEXT NOT NULL UNIQUE,
                    worker_public_key TEXT NOT NULL REFERENCES identities(public_key),
                    generator_public_key TEXT NOT NULL REFERENCES identities(public_key),
                    model_name TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    layer_start INTEGER NOT NULL,
                    layer_end INTEGER NOT NULL,
                    position_count INTEGER NOT NULL,
                    reward_units INTEGER NOT NULL,
                    settlement_mode TEXT NOT NULL,
                    submission_json TEXT NOT NULL,
                    accepted_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_key TEXT NOT NULL REFERENCES identities(public_key),
                    request_id TEXT NOT NULL UNIQUE REFERENCES receipt_pairs(request_id),
                    amount INTEGER NOT NULL,
                    reward_version INTEGER NOT NULL REFERENCES policy_versions(reward_version),
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_account_cursor
                    ON ledger_entries(public_key, id DESC);
                """
            )
            policy = default_policy()
            connection.execute(
                "INSERT OR IGNORE INTO policy_versions(reward_version, policy_json, created_at) "
                "VALUES (?, ?, ?)",
                (
                    int(policy["reward_version"]),
                    json.dumps(policy, sort_keys=True, separators=(",", ":")),
                    int(time.time()),
                ),
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()

    def active_policy(self) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT policy_json FROM policy_versions ORDER BY reward_version DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Settlement policy is not initialized")
        return json.loads(row["policy_json"])

    def append(self, verified: VerifiedReceipt, mode: SettlementMode, now: int) -> dict[str, Any]:
        worker_presence = verified.submission["worker_presence"]
        generator_presence = verified.submission["generator_presence"]
        try:
            with self.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for public_key, peer_id, presence in (
                    (verified.worker_public_key, verified.worker_peer_id, worker_presence),
                    (
                        verified.generator_public_key,
                        verified.generator_peer_id,
                        generator_presence,
                    ),
                ):
                    connection.execute(
                        "INSERT OR IGNORE INTO identities"
                        "(public_key, first_seen_at) VALUES (?, ?)",
                        (public_key, now),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO presence_bindings"
                        "(public_key, p2p_peer_id, first_seen_at) VALUES (?, ?, ?)",
                        (public_key, peer_id, now),
                    )
                    binding = connection.execute(
                        "SELECT public_key FROM presence_bindings WHERE p2p_peer_id = ?",
                        (peer_id,),
                    ).fetchone()
                    if binding is None or binding["public_key"] != public_key:
                        raise ProtocolError(
                            "P2P peer ID is already bound to another application identity"
                        )
                    connection.execute(
                        "INSERT OR IGNORE INTO presence_records"
                        "(presence_hash, public_key, p2p_peer_id, presence_json, observed_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            hash_document(presence),
                            public_key,
                            peer_id,
                            json.dumps(presence, sort_keys=True, separators=(",", ":")),
                            now,
                        ),
                    )

                connection.execute(
                    """
                    INSERT INTO receipt_pairs(
                        request_id, receipt_hash, worker_nonce, worker_public_key,
                        generator_public_key, model_name, model_revision, route_id,
                        session_id, layer_start, layer_end, position_count, reward_units,
                        settlement_mode, submission_json, accepted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verified.request_id,
                        verified.receipt_hash,
                        verified.worker_nonce,
                        verified.worker_public_key,
                        verified.generator_public_key,
                        verified.model_name,
                        verified.model_revision,
                        verified.route_id,
                        verified.session_id,
                        verified.layer_start,
                        verified.layer_end,
                        verified.position_count,
                        verified.reward_units,
                        mode,
                        json.dumps(
                            verified.submission,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )
                if mode == "credit":
                    connection.execute(
                        "INSERT INTO ledger_entries"
                        "(public_key, request_id, amount, reward_version, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            verified.worker_public_key,
                            verified.request_id,
                            verified.reward_units,
                            verified.reward_version,
                            now,
                        ),
                    )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ProtocolError("Receipt replay or identity collision rejected") from exc
        return {
            "status": "credited" if mode == "credit" else "shadow_accepted",
            "request_id": verified.request_id,
            "receipt_hash": verified.receipt_hash,
            "reward_units": verified.reward_units,
            "balance_changed": mode == "credit",
        }

    def account(self, public_key: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS balance, COUNT(*) AS entries
                FROM ledger_entries WHERE public_key = ?
                """,
                (public_key,),
            ).fetchone()
            receipts = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(position_count), 0) AS positions "
                "FROM receipt_pairs WHERE worker_public_key = ?",
                (public_key,),
            ).fetchone()
        return {
            "public_key": public_key,
            "verified_credits": int(row["balance"]),
            "ledger_entries": int(row["entries"]),
            "accepted_receipts": int(receipts["count"]),
            "useful_positions_served": int(receipts["positions"]),
        }

    def entries(self, public_key: str, cursor: int | None, limit: int) -> dict[str, Any]:
        query = (
            "SELECT id, request_id, amount, reward_version, created_at "
            "FROM ledger_entries WHERE public_key = ?"
        )
        params: list[Any] = [public_key]
        if cursor is not None:
            query += " AND id < ?"
            params.append(cursor)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit + 1)
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "entries": [dict(row) for row in page],
            "next_cursor": int(page[-1]["id"]) if has_more and page else None,
        }


def create_settlement_app(
    database_path: str | Path,
    mode: SettlementMode = "off",
    *,
    timestamp_window_seconds: int = 300,
) -> FastAPI:
    store = SettlementStore(database_path)
    app = FastAPI(title="DistribLLM Useful-Work Settlement", version="1.0.0")
    app.state.store = store
    app.state.mode = parse_incentives_mode(mode)
    app.state.timestamp_window_seconds = timestamp_window_seconds

    @app.post("/v1/receipts")
    async def submit_receipt(submission: ReceiptSubmission) -> dict[str, Any]:
        if app.state.mode == "off":
            raise HTTPException(status_code=503, detail="Settlement is disabled")
        try:
            verified = validate_submission(
                submission.model_dump(),
                store.active_policy(),
                window_seconds=app.state.timestamp_window_seconds,
            )
            return store.append(verified, app.state.mode, int(time.time()))
        except ProtocolError as exc:
            status_code = 409 if "replay" in str(exc).lower() else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @app.get("/v1/accounts/{public_key}")
    async def get_account(public_key: str) -> dict[str, Any]:
        return store.account(public_key)

    @app.get("/v1/accounts/{public_key}/entries")
    async def get_entries(
        public_key: str,
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return store.entries(public_key, cursor, limit)

    @app.get("/v1/policy")
    async def get_policy() -> dict[str, Any]:
        return {"mode": app.state.mode, **store.active_policy()}

    return app


def create_default_settlement_app() -> FastAPI:
    database = Path(
        os.environ.get(
            "DISTRIBLLM_SETTLEMENT_DB",
            str(Path.home() / ".distribllm" / "settlement.sqlite3"),
        )
    )
    return create_settlement_app(
        database,
        parse_incentives_mode(os.environ.get("DISTRIBLLM_INCENTIVES_MODE", "off")),
        timestamp_window_seconds=int(
            os.environ.get("DISTRIBLLM_RECEIPT_TIMESTAMP_WINDOW", "300")
        ),
    )
