"""Versioned tensor metadata contract for stateful inference RPC operations."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any

import torch

SESSION_PROTOCOL_VERSION = 1
SESSION_METADATA_TENSOR_SIZE = 4096
SESSION_OPERATIONS = frozenset({"open", "prefill", "decode", "close", "cancel"})


class SessionProtocolError(ValueError):
    pass


def encode_session_metadata(value: Mapping[str, Any]) -> torch.Tensor:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SessionProtocolError(f"Session metadata is not canonical JSON: {exc}") from exc
    if len(encoded) > SESSION_METADATA_TENSOR_SIZE - 4:
        raise SessionProtocolError("Session metadata exceeds the fixed RPC frame")
    framed = struct.pack(">I", len(encoded)) + encoded
    tensor = torch.zeros((1, SESSION_METADATA_TENSOR_SIZE), dtype=torch.uint8)
    tensor[0, : len(framed)] = torch.tensor(list(framed), dtype=torch.uint8)
    return tensor


def decode_session_metadata(value: torch.Tensor) -> dict[str, Any]:
    if value.dim() == 1:
        value = value.unsqueeze(0)
    if value.dim() != 2 or tuple(value.shape) != (1, SESSION_METADATA_TENSOR_SIZE):
        raise SessionProtocolError(
            "Session metadata tensor must be "
            f"[1, {SESSION_METADATA_TENSOR_SIZE}], got {tuple(value.shape)}"
        )
    raw = bytes(value.detach().to(device="cpu", dtype=torch.uint8).contiguous()[0].tolist())
    length = struct.unpack(">I", raw[:4])[0]
    if length <= 0 or length > SESSION_METADATA_TENSOR_SIZE - 4:
        raise SessionProtocolError("Session metadata has an invalid length prefix")
    try:
        document = json.loads(raw[4 : 4 + length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionProtocolError("Session metadata is not valid JSON") from exc
    if not isinstance(document, dict):
        raise SessionProtocolError("Session metadata must decode to an object")
    return document


def validate_session_operation(
    document: Mapping[str, Any],
    *,
    expected_peer_id: str,
    expected_rpc_uid: str,
    expected_layer_start: int,
    expected_layer_end: int,
) -> dict[str, Any]:
    required = {
        "protocol_version",
        "operation",
        "session_id",
        "route_id",
        "request_id",
        "operation_id",
        "peer_id",
        "rpc_uid",
        "layer_start",
        "layer_end",
        "position_start",
        "token_count",
    }
    if set(document) != required:
        missing = sorted(required - set(document))
        extra = sorted(set(document) - required)
        raise SessionProtocolError(
            f"Session operation fields do not match the protocol; missing={missing}, extra={extra}"
        )
    if document["protocol_version"] != SESSION_PROTOCOL_VERSION:
        raise SessionProtocolError("Unsupported session protocol version")
    operation = document["operation"]
    if operation not in SESSION_OPERATIONS:
        raise SessionProtocolError(f"Unsupported session operation {operation!r}")
    for field in ("session_id", "route_id", "request_id", "operation_id"):
        value = document[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise SessionProtocolError(f"{field} must be a bounded non-empty string")
    if document["peer_id"] != expected_peer_id:
        raise SessionProtocolError("Session operation targets another peer")
    if document["rpc_uid"] != expected_rpc_uid:
        raise SessionProtocolError("Session operation targets another RPC UID")
    if document["layer_start"] != expected_layer_start or document["layer_end"] != expected_layer_end:
        raise SessionProtocolError("Session operation targets another layer range")
    for field in ("position_start", "token_count"):
        value = document[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SessionProtocolError(f"{field} must be a non-negative integer")
    if operation in {"prefill", "decode"} and document["token_count"] <= 0:
        raise SessionProtocolError("Tensor session operations require token_count greater than zero")
    if operation in {"open", "close", "cancel"} and document["token_count"] != 0:
        raise SessionProtocolError("Lifecycle session operations must have token_count zero")
    return dict(document)


def session_operation_document(
    *,
    operation: str,
    session_id: str,
    route_id: str,
    request_id: str,
    operation_id: str,
    peer_id: str,
    rpc_uid: str,
    layer_start: int,
    layer_end: int,
    position_start: int,
    token_count: int,
) -> dict[str, Any]:
    return {
        "protocol_version": SESSION_PROTOCOL_VERSION,
        "operation": operation,
        "session_id": session_id,
        "route_id": route_id,
        "request_id": request_id,
        "operation_id": operation_id,
        "peer_id": peer_id,
        "rpc_uid": rpc_uid,
        "layer_start": layer_start,
        "layer_end": layer_end,
        "position_start": position_start,
        "token_count": token_count,
    }
