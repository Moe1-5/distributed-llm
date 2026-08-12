"""Canonical useful-work request, receipt, and tensor commitment protocol."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import blake3
import torch

from incentives.identity import AppIdentity, canonical_json_bytes, verify_signature


PROTOCOL_VERSION = 1
MAX_RECEIPT_BYTES = 16_384


def digest(payload: dict | list) -> str:
    return blake3.blake3(canonical_json_bytes(payload)).hexdigest()


def tensor_commitment(tensor: torch.Tensor) -> str:
    detached = tensor.detach().cpu().contiguous()
    header = canonical_json_bytes(
        {"dtype": str(detached.dtype), "shape": list(detached.shape)}
    )
    raw = detached.view(torch.uint8).numpy().tobytes()
    return blake3.blake3(header + b"\0" + raw).hexdigest()


def route_id(route: list[dict]) -> str:
    normalized = [
        {
            "peer_id": str(hop["peer_id"]),
            "layer_start": int(hop["layer_start"]),
            "layer_end": int(hop["layer_end"]),
        }
        for hop in route
    ]
    return digest(normalized)


def create_work_request(
    *,
    identity: AppIdentity,
    session_id: str,
    model_name: str,
    model_revision: str,
    route: list[dict],
    peer_id: str,
    layer_start: int,
    layer_end: int,
    hidden_states: torch.Tensor,
) -> dict:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(uuid4()),
        "session_id": session_id,
        "route_id": route_id(route),
        "route": route,
        "model_name": model_name,
        "model_revision": model_revision,
        "generator_public_key": identity.public_key,
        "worker_peer_id": peer_id,
        "layer_start": int(layer_start),
        "layer_end": int(layer_end),
        "input_commitment": tensor_commitment(hidden_states),
        "nonce": str(uuid4()),
        "requested_at": time.time(),
    }
    return {"payload": payload, "signature": identity.sign(payload)}


def validate_work_request(envelope: dict, hidden_states: torch.Tensor) -> dict:
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ValueError("Malformed signed work request")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported receipt protocol version")
    public_key = str(payload.get("generator_public_key", ""))
    if not verify_signature(public_key, payload, signature):
        raise ValueError("Invalid generator work-request signature")
    if payload.get("route_id") != route_id(payload.get("route", [])):
        raise ValueError("Work request route commitment is invalid")
    if payload.get("input_commitment") != tensor_commitment(hidden_states):
        raise ValueError("Work request input commitment does not match tensor")
    return payload


def create_worker_receipt(
    *,
    identity: AppIdentity,
    request: dict,
    peer_id: str,
    output: torch.Tensor,
    position_count: int,
) -> dict:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "session_id": request["session_id"],
        "route_id": request["route_id"],
        "model_name": request["model_name"],
        "model_revision": request["model_revision"],
        "peer_id": peer_id,
        "worker_public_key": identity.public_key,
        "generator_public_key": request["generator_public_key"],
        "layer_start": int(request["layer_start"]),
        "layer_end": int(request["layer_end"]),
        "position_count": int(position_count),
        "input_commitment": request["input_commitment"],
        "output_commitment": tensor_commitment(output),
        "nonce": request["nonce"],
        "completed_at": time.time(),
    }
    return {"payload": payload, "signature": identity.sign(payload)}


def create_generator_acceptance(
    *, identity: AppIdentity, worker_receipt: dict, route: list[dict]
) -> dict:
    worker_payload = worker_receipt["payload"]
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": worker_payload["request_id"],
        "session_id": worker_payload["session_id"],
        "route_id": worker_payload["route_id"],
        "model_name": worker_payload["model_name"],
        "model_revision": worker_payload["model_revision"],
        "worker_public_key": worker_payload["worker_public_key"],
        "generator_public_key": identity.public_key,
        "worker_receipt_hash": digest(worker_payload),
        "route": route,
        "accepted": True,
        "accepted_at": time.time(),
    }
    return {"payload": payload, "signature": identity.sign(payload)}


def validate_worker_receipt(
    *,
    envelope: dict,
    request_envelope: dict,
    worker_public_key: str,
    peer_id: str,
    output: torch.Tensor,
) -> dict:
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    request = request_envelope.get("payload", {})
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ValueError("Malformed worker receipt")
    if not verify_signature(worker_public_key, payload, signature):
        raise ValueError("Invalid worker receipt signature")
    expected = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request.get("request_id"),
        "session_id": request.get("session_id"),
        "route_id": request.get("route_id"),
        "model_name": request.get("model_name"),
        "model_revision": request.get("model_revision"),
        "peer_id": peer_id,
        "worker_public_key": worker_public_key,
        "generator_public_key": request.get("generator_public_key"),
        "layer_start": request.get("layer_start"),
        "layer_end": request.get("layer_end"),
        "input_commitment": request.get("input_commitment"),
        "nonce": request.get("nonce"),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"Worker receipt {key} does not match request")
    expected_positions = int(output.shape[0] * output.shape[1])
    if int(payload.get("position_count", 0)) != expected_positions:
        raise ValueError("Worker receipt position_count does not match output tensor")
    if payload.get("output_commitment") != tensor_commitment(output):
        raise ValueError("Worker receipt output commitment does not match tensor")
    return payload


def encode_envelope(envelope: dict, *, rows: int = 1) -> torch.Tensor:
    encoded = canonical_json_bytes(envelope)
    if len(encoded) + 4 > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt metadata exceeds fixed tensor capacity")
    length = len(encoded).to_bytes(4, "big")
    row = torch.zeros(MAX_RECEIPT_BYTES, dtype=torch.uint8)
    row[: 4 + len(encoded)] = torch.tensor(list(length + encoded), dtype=torch.uint8)
    return row.unsqueeze(0).repeat(rows, 1)


def decode_envelope(row: torch.Tensor) -> dict:
    raw = bytes(row.detach().cpu().to(dtype=torch.uint8).flatten().tolist())
    size = int.from_bytes(raw[:4], "big")
    if size <= 0 or size > MAX_RECEIPT_BYTES - 4:
        raise ValueError("Invalid receipt metadata length")
    value = json.loads(raw[4 : 4 + size].decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Receipt metadata must be a JSON object")
    return value
