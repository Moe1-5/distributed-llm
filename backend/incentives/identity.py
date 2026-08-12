"""Persistent Ed25519 application identity independent from the p2p daemon key."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from incentives.protocol import (
    PROTOCOL_VERSION,
    SIGNATURE_DOMAIN,
    canonical_json,
    decode_base64,
    encode_base64,
)

_identity_cache: dict[Path, "ApplicationIdentity"] = {}
_identity_lock = threading.Lock()


def default_identity_path() -> Path:
    configured = os.environ.get("DISTRIBLLM_IDENTITY_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".distribllm" / "application-identity.json").resolve()


@dataclass(frozen=True)
class ApplicationIdentity:
    _private_key: Ed25519PrivateKey
    path: Path

    @property
    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return encode_base64(raw)

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        signed_payload = {
            **payload,
            "protocol_version": PROTOCOL_VERSION,
            "signer_public_key": self.public_key,
        }
        signature = self._private_key.sign(
            SIGNATURE_DOMAIN + canonical_json(signed_payload)
        )
        return {
            "payload": signed_payload,
            "public_key": self.public_key,
            "signature": encode_base64(signature),
        }

    def presence(self, p2p_peer_id: str, *, timestamp: int | None = None) -> dict[str, Any]:
        if not p2p_peer_id.strip():
            raise ValueError("p2p_peer_id must not be empty")
        return self.sign(
            {
                "document_type": "presence",
                "application_public_key": self.public_key,
                "p2p_peer_id": p2p_peer_id,
                "timestamp": int(time.time()) if timestamp is None else int(timestamp),
                "nonce": secrets.token_hex(16),
            }
        )


def _write_identity(path: Path, private_key: Ed25519PrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    document = {
        "schema_version": 1,
        "private_key": encode_base64(raw),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _read_identity(path: Path) -> Ed25519PrivateKey:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Application identity must be a regular file")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Application identity permissions must not allow group or other access")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1:
            raise RuntimeError("Unsupported application identity schema")
        raw = decode_base64(document["private_key"])
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Application identity file is invalid") from exc


def load_application_identity(path: str | Path | None = None) -> ApplicationIdentity:
    target = Path(path).expanduser().resolve() if path is not None else default_identity_path()
    with _identity_lock:
        cached = _identity_cache.get(target)
        if cached is not None:
            return cached
        if target.exists():
            private_key = _read_identity(target)
        else:
            private_key = Ed25519PrivateKey.generate()
            _write_identity(target, private_key)
        identity = ApplicationIdentity(private_key, target)
        _identity_cache[target] = identity
        return identity
