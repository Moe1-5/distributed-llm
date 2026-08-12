"""Construction and validation helpers for useful-work receipt protocol v1."""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

import torch

from incentives.identity import ApplicationIdentity
from incentives.protocol import (
    ProtocolError,
    hash_document,
    normalize_route,
    route_id,
    tensor_commitment,
    validate_position_count,
    verify_signed_document,
)


def _text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


def _timestamp(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def verify_presence(
    document: dict[str, Any],
    *,
    public_key: str,
    peer_id: str,
    now: int | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    payload = verify_signed_document(document, expected_type="presence")
    if document["public_key"] != public_key:
        raise ProtocolError("Presence signature key does not match the advertised key")
    if payload.get("application_public_key") != public_key:
        raise ProtocolError("Presence application key is inconsistent")
    if payload.get("p2p_peer_id") != peer_id:
        raise ProtocolError("Presence p2p peer ID is inconsistent")
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, bool):
        raise ProtocolError("Presence timestamp must be an integer")
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Presence timestamp must be an integer") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max_age_seconds:
        raise ProtocolError("Presence timestamp is outside the allowed window")
    _text(payload, "nonce")
    return payload


def create_inference_request(
    identity: ApplicationIdentity,
    *,
    generator_peer_id: str,
    session_id: str,
    model_name: str,
    model_revision: str,
    route: list[dict[str, Any]],
    worker: dict[str, Any],
    hidden_states: torch.Tensor,
    position_count: int,
    request_id: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    normalized_route = normalize_route(route)
    try:
        worker_key = {
            "peer_id": str(worker["peer_id"]),
            "application_public_key": str(worker["application_public_key"]),
            "rpc_uid": str(worker["rpc_uid"]),
            "layer_start": int(worker["layer_start"]),
            "layer_end": int(worker["layer_end"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("Requested worker metadata is invalid") from exc
    if worker_key not in normalized_route:
        raise ProtocolError("Requested worker is not a member of the selected route")
    positions = validate_position_count(position_count)
    if hidden_states.dim() < 2 or positions != int(
        hidden_states.shape[0] * hidden_states.shape[1]
    ):
        raise ProtocolError("position_count does not match the input tensor")
    return identity.sign(
        {
            "document_type": "inference_request",
            "request_id": request_id or str(uuid.uuid4()),
            "session_id": session_id,
            "route_id": route_id(normalized_route),
            "selected_route": normalized_route,
            "generator_peer_id": generator_peer_id,
            "generator_public_key": identity.public_key,
            "worker_peer_id": worker_key["peer_id"],
            "worker_public_key": worker_key["application_public_key"],
            "rpc_uid": worker_key["rpc_uid"],
            "model_name": model_name,
            "model_revision": model_revision,
            "layer_start": worker_key["layer_start"],
            "layer_end": worker_key["layer_end"],
            "position_count": positions,
            "input_shape": list(hidden_states.shape),
            "input_commitment": tensor_commitment(hidden_states),
            "nonce": secrets.token_hex(16),
            "timestamp": _timestamp(timestamp),
        }
    )


def verify_inference_request(
    document: dict[str, Any],
    *,
    worker_public_key: str,
    worker_peer_id: str,
    rpc_uid: str,
    layer_start: int,
    layer_end: int,
    hidden_states: torch.Tensor,
) -> dict[str, Any]:
    payload = verify_signed_document(document, expected_type="inference_request")
    if document["public_key"] != payload.get("generator_public_key"):
        raise ProtocolError("Inference request generator key is inconsistent")
    expected = {
        "worker_public_key": worker_public_key,
        "worker_peer_id": worker_peer_id,
        "rpc_uid": rpc_uid,
        "layer_start": layer_start,
        "layer_end": layer_end,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ProtocolError(f"Inference request {field} does not match this worker")
    positions = validate_position_count(payload.get("position_count"))
    if hidden_states.dim() < 2 or positions != int(
        hidden_states.shape[0] * hidden_states.shape[1]
    ):
        raise ProtocolError("Inference request position_count does not match its tensor")
    if payload.get("input_commitment") != tensor_commitment(hidden_states):
        raise ProtocolError("Inference request input commitment is invalid")
    if payload.get("input_shape") != list(hidden_states.shape):
        raise ProtocolError("Inference request input shape is invalid")
    selected_route = normalize_route(payload.get("selected_route"))
    if route_id(selected_route) != payload.get("route_id"):
        raise ProtocolError("Inference request selected route does not match its route ID")
    worker_member = {
        "peer_id": worker_peer_id,
        "application_public_key": worker_public_key,
        "rpc_uid": rpc_uid,
        "layer_start": layer_start,
        "layer_end": layer_end,
    }
    if worker_member not in selected_route:
        raise ProtocolError("This worker is not a member of the signed selected route")
    for field in (
        "request_id",
        "session_id",
        "route_id",
        "model_name",
        "model_revision",
    ):
        _text(payload, field)
    return payload


def create_worker_receipt(
    identity: ApplicationIdentity,
    request: dict[str, Any],
    response: torch.Tensor,
    *,
    worker_peer_id: str,
    timestamp: int | None = None,
) -> dict[str, Any]:
    if request.get("worker_public_key") != identity.public_key:
        raise ProtocolError("Inference request targets a different worker identity")
    if request.get("worker_peer_id") != worker_peer_id:
        raise ProtocolError("Inference request targets a different worker peer")
    copied = {
        field: request[field]
        for field in (
            "request_id",
            "session_id",
            "route_id",
            "selected_route",
            "generator_peer_id",
            "generator_public_key",
            "worker_peer_id",
            "worker_public_key",
            "rpc_uid",
            "model_name",
            "model_revision",
            "layer_start",
            "layer_end",
            "position_count",
            "input_shape",
            "input_commitment",
        )
    }
    return identity.sign(
        {
            "document_type": "worker_receipt",
            **copied,
            "response_commitment": tensor_commitment(response),
            "nonce": secrets.token_hex(16),
            "timestamp": _timestamp(timestamp),
        }
    )


def accept_worker_receipt(
    identity: ApplicationIdentity,
    request_document: dict[str, Any],
    receipt_document: dict[str, Any],
    response: torch.Tensor,
    *,
    generator_peer_id: str,
    timestamp: int | None = None,
) -> dict[str, Any]:
    request = verify_signed_document(request_document, expected_type="inference_request")
    receipt = verify_signed_document(receipt_document, expected_type="worker_receipt")
    if request_document["public_key"] != identity.public_key:
        raise ProtocolError("Inference request was signed by a different generator")
    paired_fields = (
        "request_id",
        "session_id",
        "route_id",
        "selected_route",
        "generator_peer_id",
        "generator_public_key",
        "worker_peer_id",
        "worker_public_key",
        "rpc_uid",
        "model_name",
        "model_revision",
        "layer_start",
        "layer_end",
        "position_count",
        "input_shape",
        "input_commitment",
    )
    for field in paired_fields:
        if receipt.get(field) != request.get(field):
            raise ProtocolError(f"Worker receipt {field} does not match the request")
    if receipt_document["public_key"] != request.get("worker_public_key"):
        raise ProtocolError("Worker receipt was signed by an unexpected identity")
    if generator_peer_id != request.get("generator_peer_id"):
        raise ProtocolError("Generator peer ID does not match the request")
    if receipt.get("response_commitment") != tensor_commitment(response):
        raise ProtocolError("Worker receipt response commitment is invalid")
    if list(response.shape) != request.get("input_shape"):
        raise ProtocolError("Worker response shape does not match the request")
    if not torch.isfinite(response).all().item():
        raise ProtocolError("Worker response contains non-finite values")
    return identity.sign(
        {
            "document_type": "generator_acceptance",
            "request_id": request["request_id"],
            "session_id": request["session_id"],
            "route_id": request["route_id"],
            "generator_peer_id": generator_peer_id,
            "generator_public_key": identity.public_key,
            "worker_public_key": request["worker_public_key"],
            "worker_receipt_hash": hash_document(receipt_document),
            "accepted": True,
            "timestamp": _timestamp(timestamp),
        }
    )


def build_submission(
    *,
    worker_receipt: dict[str, Any],
    generator_acceptance: dict[str, Any],
    worker_presence: dict[str, Any],
    generator_presence: dict[str, Any],
    route: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "worker_receipt": worker_receipt,
        "generator_acceptance": generator_acceptance,
        "worker_presence": worker_presence,
        "generator_presence": generator_presence,
        "route": normalize_route(route),
    }
