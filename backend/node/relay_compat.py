"""Compatibility helpers for Hivemind's bundled p2pd relay behavior."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from hivemind.p2p import P2P
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)

_PATCH_MARKER = "_distribllm_static_relay_patch"


def _static_relay_process_args(
    original: Callable[..., list[str]],
    *args: Any,
    **kwargs: Any,
) -> list[str]:
    """Select p2pd's static-relay path when trusted relays are configured."""
    process_kwargs = dict(kwargs)
    if process_kwargs.get("autoRelay") and process_kwargs.get("trustedRelays"):
        process_kwargs["relayDiscovery"] = False
    return original(*args, **process_kwargs)


def install_static_relay_compat() -> None:
    """Make one configured VPS usable without waiting for relay candidates."""
    current = P2P._make_process_args
    if getattr(current, _PATCH_MARKER, False):
        return

    @wraps(current)
    def patched(*args: Any, **kwargs: Any) -> list[str]:
        return _static_relay_process_args(current, *args, **kwargs)

    setattr(patched, _PATCH_MARKER, True)
    P2P._make_process_args = staticmethod(patched)
    logger.info(
        "Installed trusted-relay compatibility: p2pd relay discovery is "
        "disabled when static trusted relays are configured"
    )
