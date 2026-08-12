"""Bounded complete-attempt failover policy for distributed forwards."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteFailoverConfig:
    max_attempts: int = 2
    max_alternates: int = 3
    backoff_seconds: float = 0.25
    quarantine_seconds: float = 15.0
    allow_degraded: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("Route failover attempts must be between 1 and 5")
        if not 0 <= self.max_alternates <= 8:
            raise ValueError("Route alternates must be between 0 and 8")
        if not 0 <= self.backoff_seconds <= 30:
            raise ValueError("Route failover backoff must be between 0 and 30 seconds")
        if not 0 <= self.quarantine_seconds <= 3600:
            raise ValueError("Route quarantine must be between 0 and 3600 seconds")

    def backoff_for_attempt(self, completed_attempts: int) -> float:
        return self.backoff_seconds * max(1, completed_attempts)


class RouteAttemptError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        node: dict,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.node = dict(node)
        self.__cause__ = cause


class RouteCancellationError(RuntimeError):
    """Raised when local cancellation prevents further route work."""


def get_route_failover_config() -> RouteFailoverConfig:
    return RouteFailoverConfig(
        max_attempts=int(os.getenv("DISTRIBLLM_ROUTE_MAX_ATTEMPTS", "2")),
        max_alternates=int(os.getenv("DISTRIBLLM_ROUTE_MAX_ALTERNATES", "3")),
        backoff_seconds=float(os.getenv("DISTRIBLLM_ROUTE_BACKOFF_SECONDS", "0.25")),
        quarantine_seconds=float(os.getenv("DISTRIBLLM_ROUTE_QUARANTINE_SECONDS", "15")),
        allow_degraded=os.getenv("DISTRIBLLM_ROUTE_ALLOW_DEGRADED", "true").strip().lower()
        in {"1", "true", "yes", "on"},
    )
