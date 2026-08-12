"""Persistent Ed25519 application identity and canonical signing helpers."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_json_bytes(payload: dict | list) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)


def default_identity_dir() -> Path:
    root = Path(
        os.environ.get(
            "DISTRIBLLM_DATA_DIR",
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "distribllm",
        )
    ).expanduser()
    return root / "identity"


@dataclass(frozen=True)
class AppIdentity:
    _private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64encode(raw)

    def sign(self, payload: dict | list) -> str:
        return _b64encode(self._private_key.sign(canonical_json_bytes(payload)))


def get_or_create_identity(identity_dir: Path | None = None) -> AppIdentity:
    directory = identity_dir or default_identity_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = directory / "ed25519.key"
    if key_path.exists():
        raw = key_path.read_bytes()
        if len(raw) != 32:
            raise RuntimeError(f"Invalid DistribLLM identity key length in {key_path}")
        return AppIdentity(Ed25519PrivateKey.from_private_bytes(raw))

    private_key = Ed25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    temporary = key_path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return AppIdentity(private_key)


def verify_signature(public_key: str, payload: dict | list, signature: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
        key.verify(_b64decode(signature), canonical_json_bytes(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def validate_public_key(public_key: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
        return True
    except (ValueError, TypeError):
        return False
