"""Canonical signed documents and tensor commitments for receipt protocol v1."""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping
from typing import Any

import torch
from blake3 import blake3
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROTOCOL_VERSION = 1
METADATA_TENSOR_SIZE = 16_384
SIGNATURE_DOMAIN = b"distribllm-useful-work-v1\x00"


class ProtocolError(ValueError):
    """Raised when receipt metadata is malformed or cryptographically invalid."""


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Document is not canonical JSON data: {exc}") from exc


def encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ProtocolError("Expected a non-empty base64url string")
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ProtocolError("Invalid base64url value") from exc


def hash_bytes(value: bytes) -> str:
    return blake3(value).hexdigest()


def hash_document(value: Mapping[str, Any]) -> str:
    return hash_bytes(canonical_json(value))


def verify_signed_document(
    document: Mapping[str, Any],
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    if set(document) != {"payload", "public_key", "signature"}:
        raise ProtocolError("Signed document must contain payload, public_key, and signature")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("Signed document payload must be an object")
    public_key_text = document.get("public_key")
    signature_text = document.get("signature")
    if not isinstance(public_key_text, str) or not isinstance(signature_text, str):
        raise ProtocolError("Signed document key and signature must be strings")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(decode_base64(public_key_text))
        public_key.verify(
            decode_base64(signature_text),
            SIGNATURE_DOMAIN + canonical_json(payload),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ProtocolError("Signed document signature is invalid") from exc
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported receipt protocol version")
    if expected_type is not None and payload.get("document_type") != expected_type:
        raise ProtocolError(f"Expected {expected_type} signed document")
    if payload.get("signer_public_key") != public_key_text:
        raise ProtocolError("Payload signer_public_key does not match signature key")
    return dict(payload)


def encode_metadata_tensor(value: Mapping[str, Any]) -> torch.Tensor:
    encoded = canonical_json(value)
    if len(encoded) > METADATA_TENSOR_SIZE - 4:
        raise ProtocolError(
            f"Receipt metadata exceeds {METADATA_TENSOR_SIZE - 4} bytes"
        )
    framed = struct.pack(">I", len(encoded)) + encoded
    tensor = torch.zeros((1, METADATA_TENSOR_SIZE), dtype=torch.uint8)
    tensor[0, : len(framed)] = torch.tensor(list(framed), dtype=torch.uint8)
    return tensor


def decode_metadata_tensor(value: torch.Tensor) -> dict[str, Any]:
    if value.dim() == 1:
        value = value.unsqueeze(0)
    if value.dim() != 2 or value.shape[0] != 1 or value.shape[1] != METADATA_TENSOR_SIZE:
        raise ProtocolError(
            f"Metadata tensor must be [1, {METADATA_TENSOR_SIZE}], got {tuple(value.shape)}"
        )
    raw = bytes(value.detach().to(device="cpu", dtype=torch.uint8).contiguous()[0].tolist())
    length = struct.unpack(">I", raw[:4])[0]
    if length <= 0 or length > METADATA_TENSOR_SIZE - 4:
        raise ProtocolError("Metadata tensor contains an invalid payload length")
    try:
        decoded = json.loads(raw[4 : 4 + length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Metadata tensor does not contain valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError("Metadata tensor JSON must be an object")
    return decoded


def split_metadata_batch(value: torch.Tensor) -> list[dict[str, Any]]:
    if value.dim() != 2 or value.shape[1] != METADATA_TENSOR_SIZE:
        raise ProtocolError(
            f"Metadata batch must be [batch, {METADATA_TENSOR_SIZE}], got {tuple(value.shape)}"
        )
    return [decode_metadata_tensor(value[index : index + 1]) for index in range(value.shape[0])]


def join_metadata_batch(values: list[Mapping[str, Any]]) -> torch.Tensor:
    if not values:
        raise ProtocolError("Cannot encode an empty metadata batch")
    return torch.cat([encode_metadata_tensor(value) for value in values], dim=0)


def tensor_commitment(value: torch.Tensor) -> str:
    tensor = value.detach().to(device="cpu").contiguous()
    descriptor = canonical_json(
        {
            "dtype": str(tensor.dtype),
            "shape": list(tensor.shape),
        }
    )
    hasher = blake3()
    hasher.update(struct.pack(">I", len(descriptor)))
    hasher.update(descriptor)
    hasher.update(tensor.view(torch.uint8).numpy().tobytes(order="C"))
    return hasher.hexdigest()


def validate_commitment(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ProtocolError(f"{field} must be a BLAKE3 hex digest")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ProtocolError(f"{field} must be a BLAKE3 hex digest") from exc
    return value


def normalize_route(route: Any) -> list[dict[str, Any]]:
    if not isinstance(route, list) or not route:
        raise ProtocolError("Selected route must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    expected_start = 0
    for member in route:
        if not isinstance(member, dict):
            raise ProtocolError("Route members must be objects")
        try:
            start = member["layer_start"]
            end = member["layer_end"]
        except KeyError as exc:
            raise ProtocolError("Route member has invalid layer bounds") from exc
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
        ):
            raise ProtocolError("Route member layer bounds must be integers")
        if start != expected_start or end <= start:
            raise ProtocolError("Selected route must contain exactly adjacent layer ranges")
        normalized.append(
            {
                "peer_id": str(member.get("peer_id", "")),
                "application_public_key": str(
                    member.get("application_public_key", "")
                ),
                "rpc_uid": str(member.get("rpc_uid", "")),
                "layer_start": start,
                "layer_end": end,
            }
        )
        if not all(
            (
                normalized[-1]["peer_id"],
                normalized[-1]["application_public_key"],
                normalized[-1]["rpc_uid"],
            )
        ):
            raise ProtocolError("Route member identity and RPC UID are required")
        expected_start = end
    return normalized


def route_id(route: Any) -> str:
    return hash_document({"route": normalize_route(route)})


def validate_position_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError("position_count must be a positive integer")
    return value
