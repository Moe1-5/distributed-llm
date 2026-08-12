"""VPS-deployable append-only useful-work settlement service."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constants import SUPPORTED_MODELS
from incentives.identity import validate_public_key, verify_signature
from incentives.protocol import PROTOCOL_VERSION, digest, route_id


class ReceiptPair(BaseModel):
    worker_receipt: dict
    generator_acceptance: dict


def default_database_path() -> Path:
    data_dir = Path(
        os.environ.get(
            "DISTRIBLLM_SETTLEMENT_DATA_DIR",
            Path.home() / ".local" / "share" / "distribllm-settlement",
        )
    ).expanduser()
    return data_dir / "ledger.sqlite3"


class SettlementPolicy:
    def __init__(self, mode: Optional[str] = None):
        self.mode = (mode or os.environ.get("DISTRIBLLM_SETTLEMENT_MODE", "shadow")).strip().lower()
        if self.mode not in {"shadow", "credit"}:
            raise ValueError("DISTRIBLLM_SETTLEMENT_MODE must be shadow or credit")
        self.version = os.environ.get("DISTRIBLLM_REWARD_POLICY_VERSION", "1").strip() or "1"
        self.reward_scale = int(os.environ.get("DISTRIBLLM_REWARD_SCALE", "1"))
        if self.reward_scale <= 0:
            raise ValueError("DISTRIBLLM_REWARD_SCALE must be positive")
        weights = json.loads(os.environ.get("DISTRIBLLM_MODEL_COMPUTE_WEIGHTS", "{}"))
        revisions = json.loads(os.environ.get("DISTRIBLLM_MODEL_REVISIONS", "{}"))
        self.model_weights = {
            model: int(weights.get(model, 1))
            for model in SUPPORTED_MODELS
        }
        if any(weight <= 0 for weight in self.model_weights.values()):
            raise ValueError("Every model compute weight must be positive")
        self.model_revisions = {
            model: set(revisions.get(model, ["registry"]))
            for model in SUPPORTED_MODELS
        }

    def as_dict(self) -> dict:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "policy_version": self.version,
            "mode": self.mode,
            "reward_scale": self.reward_scale,
            "model_compute_weights": self.model_weights,
            "model_revisions": {
                model: sorted(values) for model, values in self.model_revisions.items()
            },
            "formula": "position_count * served_layer_count * model_compute_weight * reward_scale",
        }


class SettlementStore:
    def __init__(self, database_path: Path, policy: SettlementPolicy):
        self.database_path = database_path
        self.policy = policy
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipt_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    worker_public_key TEXT NOT NULL,
                    generator_public_key TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    layer_start INTEGER NOT NULL,
                    layer_end INTEGER NOT NULL,
                    position_count INTEGER NOT NULL,
                    calculated_reward INTEGER NOT NULL,
                    settlement_mode TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    submitted_at REAL NOT NULL,
                    worker_receipt_json TEXT NOT NULL,
                    generator_acceptance_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_key TEXT NOT NULL,
                    delta INTEGER NOT NULL CHECK(delta > 0),
                    receipt_hash TEXT NOT NULL UNIQUE,
                    policy_version TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_account_index
                    ON ledger_entries(public_key, id);
                CREATE TABLE IF NOT EXISTS identities (
                    public_key TEXT PRIMARY KEY,
                    first_seen_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_versions (
                    version TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO policy_versions (version, policy_json, created_at) "
                "VALUES (?, ?, ?)",
                (self.policy.version, json.dumps(self.policy.as_dict(), sort_keys=True), time.time()),
            )

    def submit(self, pair: dict) -> dict:
        validated = validate_receipt_pair(pair, self.policy)
        receipt_hash = digest(pair)
        reward = (
            validated["position_count"]
            * (validated["layer_end"] - validated["layer_start"])
            * self.policy.model_weights[validated["model_name"]]
            * self.policy.reward_scale
        )
        now = time.time()
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO identities (public_key, first_seen_at) VALUES (?, ?)",
                    (validated["worker_public_key"], now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO identities (public_key, first_seen_at) VALUES (?, ?)",
                    (validated["generator_public_key"], now),
                )
                connection.execute(
                    """
                    INSERT INTO receipt_pairs (
                        request_id, receipt_hash, worker_public_key, generator_public_key,
                        model_name, model_revision, peer_id, layer_start, layer_end,
                        position_count, calculated_reward, settlement_mode, policy_version,
                        submitted_at, worker_receipt_json, generator_acceptance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        validated["request_id"], receipt_hash,
                        validated["worker_public_key"], validated["generator_public_key"],
                        validated["model_name"], validated["model_revision"], validated["peer_id"],
                        validated["layer_start"], validated["layer_end"], validated["position_count"],
                        reward, self.policy.mode, self.policy.version, now,
                        json.dumps(pair["worker_receipt"], sort_keys=True),
                        json.dumps(pair["generator_acceptance"], sort_keys=True),
                    ),
                )
                if self.policy.mode == "credit":
                    connection.execute(
                        """
                        INSERT INTO ledger_entries
                            (public_key, delta, receipt_hash, policy_version, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            validated["worker_public_key"], reward, receipt_hash,
                            self.policy.version, now,
                        ),
                    )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ValueError("Duplicate request or receipt") from exc
        return {
            "accepted": True,
            "request_id": validated["request_id"],
            "receipt_hash": receipt_hash,
            "mode": self.policy.mode,
            "calculated_reward": reward,
            "credited": reward if self.policy.mode == "credit" else 0,
        }

    def account(self, public_key: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(delta), 0) AS balance, COUNT(*) AS entry_count "
                "FROM ledger_entries WHERE public_key = ?",
                (public_key,),
            ).fetchone()
        return {
            "public_key": public_key,
            "verified_credits": int(row["balance"]),
            "entry_count": int(row["entry_count"]),
            "mode": self.policy.mode,
        }

    def entries(self, public_key: str, cursor: int, limit: int) -> dict:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, delta, receipt_hash, policy_version, created_at
                FROM ledger_entries
                WHERE public_key = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (public_key, cursor, limit),
            ).fetchall()
        entries = [dict(row) for row in rows]
        return {
            "public_key": public_key,
            "entries": entries,
            "next_cursor": entries[-1]["id"] if entries else None,
        }


def validate_receipt_pair(pair: dict, policy: SettlementPolicy) -> dict:
    worker = pair.get("worker_receipt", {})
    acceptance = pair.get("generator_acceptance", {})
    worker_payload = worker.get("payload")
    acceptance_payload = acceptance.get("payload")
    if not isinstance(worker_payload, dict) or not isinstance(acceptance_payload, dict):
        raise ValueError("Receipt pair payloads are required")

    worker_key = str(worker_payload.get("worker_public_key", ""))
    generator_key = str(worker_payload.get("generator_public_key", ""))
    if not worker_key or not generator_key or worker_key == generator_key:
        raise ValueError("Worker and generator identities must be distinct")
    if not verify_signature(worker_key, worker_payload, str(worker.get("signature", ""))):
        raise ValueError("Invalid worker signature")
    if not verify_signature(generator_key, acceptance_payload, str(acceptance.get("signature", ""))):
        raise ValueError("Invalid generator signature")
    if worker_payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported worker receipt protocol")
    if acceptance_payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported generator acceptance protocol")
    if acceptance_payload.get("accepted") is not True:
        raise ValueError("Generator did not accept the work")

    shared_fields = ("request_id", "session_id", "route_id", "model_name", "model_revision")
    for field in shared_fields:
        if worker_payload.get(field) != acceptance_payload.get(field):
            raise ValueError(f"Receipt pair {field} mismatch")
    if acceptance_payload.get("worker_public_key") != worker_key:
        raise ValueError("Acceptance worker identity mismatch")
    if acceptance_payload.get("generator_public_key") != generator_key:
        raise ValueError("Acceptance generator identity mismatch")
    if acceptance_payload.get("worker_receipt_hash") != digest(worker_payload):
        raise ValueError("Acceptance does not commit to the worker receipt")

    route = acceptance_payload.get("route")
    try:
        committed_route_id = route_id(route) if isinstance(route, list) else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Acceptance route is malformed") from exc
    if not isinstance(route, list) or acceptance_payload.get("route_id") != committed_route_id:
        raise ValueError("Acceptance route commitment is invalid")
    peer_id = str(worker_payload.get("peer_id", ""))
    layer_start = int(worker_payload.get("layer_start", -1))
    layer_end = int(worker_payload.get("layer_end", -1))
    if not any(
        str(hop.get("peer_id")) == peer_id
        and int(hop.get("layer_start", -1)) == layer_start
        and int(hop.get("layer_end", -1)) == layer_end
        for hop in route
        if isinstance(hop, dict)
    ):
        raise ValueError("Worker is not a member of the accepted route")

    model_name = str(worker_payload.get("model_name", ""))
    model_revision = str(worker_payload.get("model_revision", ""))
    if model_name not in SUPPORTED_MODELS:
        raise ValueError("Model is not settlement-eligible")
    if model_revision not in policy.model_revisions[model_name]:
        raise ValueError("Model revision is not settlement-eligible")
    total_layers = int(SUPPORTED_MODELS[model_name]["num_layers"])
    if not 0 <= layer_start < layer_end <= total_layers:
        raise ValueError("Receipt layer range is outside the model")
    cursor = 0
    for hop in route:
        hop_start = int(hop["layer_start"])
        hop_end = int(hop["layer_end"])
        if hop_start != cursor or not hop_start < hop_end <= total_layers:
            raise ValueError("Accepted route is not contiguous and non-overlapping")
        cursor = hop_end
    if cursor != total_layers:
        raise ValueError("Accepted route does not cover the complete model")
    position_count = int(worker_payload.get("position_count", 0))
    if position_count <= 0:
        raise ValueError("Receipt position_count must be positive")
    completed_at = float(worker_payload.get("completed_at", 0))
    accepted_at = float(acceptance_payload.get("accepted_at", 0))
    now = time.time()
    if abs(now - completed_at) > 300 or abs(now - accepted_at) > 300:
        raise ValueError("Receipt timestamp is outside the five-minute window")

    return {
        "request_id": str(worker_payload["request_id"]),
        "worker_public_key": worker_key,
        "generator_public_key": generator_key,
        "model_name": model_name,
        "model_revision": model_revision,
        "peer_id": peer_id,
        "layer_start": layer_start,
        "layer_end": layer_end,
        "position_count": position_count,
    }


def create_settlement_app(
    *, database_path: Optional[Path] = None, mode: Optional[str] = None
) -> FastAPI:
    policy = SettlementPolicy(mode)
    settlement_app = FastAPI(title="DistribLLM Settlement", version="1.0")
    settlement_app.state.store = (
        SettlementStore(database_path, policy) if database_path is not None else None
    )
    settlement_app.state.store_lock = threading.Lock()

    def get_store() -> SettlementStore:
        if settlement_app.state.store is None:
            with settlement_app.state.store_lock:
                if settlement_app.state.store is None:
                    settlement_app.state.store = SettlementStore(default_database_path(), policy)
        return settlement_app.state.store

    @settlement_app.post("/v1/receipts")
    def submit_receipt(pair: ReceiptPair) -> dict:
        try:
            return get_store().submit(pair.model_dump())
        except ValueError as exc:
            status = 409 if "Duplicate" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @settlement_app.get("/v1/accounts/{public_key}")
    def get_account(public_key: str) -> dict:
        if not validate_public_key(public_key):
            raise HTTPException(status_code=422, detail="Invalid Ed25519 public key")
        return get_store().account(public_key)

    @settlement_app.get("/v1/accounts/{public_key}/entries")
    def get_entries(public_key: str, cursor: int = 0, limit: int = 50) -> dict:
        if not validate_public_key(public_key):
            raise HTTPException(status_code=422, detail="Invalid Ed25519 public key")
        if cursor < 0 or not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="cursor must be non-negative and limit 1-200")
        return get_store().entries(public_key, cursor, limit)

    @settlement_app.get("/v1/policy")
    def get_policy() -> dict:
        return policy.as_dict()

    return settlement_app


app = create_settlement_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("DISTRIBLLM_SETTLEMENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("DISTRIBLLM_SETTLEMENT_PORT", "8010")),
        reload=False,
    )
