"""Validated retry policy for remote expert calls."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RPCAttemptPolicy:
    max_attempts: int = 2
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 4.0
    slow_request_warning_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("RPC max_attempts must be between 1 and 5")
        if not 0 <= self.initial_backoff_seconds <= 30:
            raise ValueError("RPC initial backoff must be between 0 and 30 seconds")
        if not self.initial_backoff_seconds <= self.max_backoff_seconds <= 60:
            raise ValueError(
                "RPC max backoff must be between initial_backoff_seconds and 60 seconds"
            )
        if not 1 <= self.slow_request_warning_seconds <= 600:
            raise ValueError("RPC slow-request warning must be between 1 and 600 seconds")

    def backoff_seconds(self, completed_attempts: int) -> float:
        if completed_attempts < 1:
            return 0.0
        return min(
            self.initial_backoff_seconds * (2 ** (completed_attempts - 1)),
            self.max_backoff_seconds,
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number; got {raw!r}") from exc


def get_rpc_attempt_policy() -> RPCAttemptPolicy:
    return RPCAttemptPolicy(
        max_attempts=_env_int("DISTRIBLLM_RPC_MAX_ATTEMPTS", 2),
        initial_backoff_seconds=_env_float(
            "DISTRIBLLM_RPC_INITIAL_BACKOFF_SECONDS", 1.0
        ),
        max_backoff_seconds=_env_float(
            "DISTRIBLLM_RPC_MAX_BACKOFF_SECONDS", 4.0
        ),
        slow_request_warning_seconds=_env_float(
            "DISTRIBLLM_RPC_SLOW_WARNING_SECONDS", 30.0
        ),
    )


def classify_rpc_error(exc: Exception) -> str:
    """Classify whether another attempt is safe before remote execution."""
    if isinstance(exc, (AssertionError, TypeError, ValueError)):
        return "invalid_request"

    message = str(exc).lower()
    ambiguous_markers = (
        "stream reset",
        "broken pipe",
        "connection closed",
        "unexpected eof",
        "timed out",
        "timeout",
    )
    if any(marker in message for marker in ambiguous_markers):
        return "ambiguous_transport"

    pre_execution_markers = (
        "not found in dht",
        "expert not found",
        "failed to connect",
        "connection refused",
        "no route to host",
        "failed to dial",
        "routing: not found",
    )
    if any(marker in message for marker in pre_execution_markers):
        return "pre_execution_transport"
    return "remote_failure"


def is_retryable_rpc_error(exc: Exception) -> bool:
    return classify_rpc_error(exc) == "pre_execution_transport"


def is_safe_receipt_fallback(exc: Exception) -> bool:
    message = str(exc).lower()
    return "receipt expert" in message and "was not found" in message
