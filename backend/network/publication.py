"""Pure classification of DHT publication evidence.

Hivemind's ``DHT.store`` returns ``False`` both when a newer record wins and
when no store acknowledgement arrives.  The return value is therefore not
transport-health evidence.  This module keeps that ambiguity explicit and
only labels a local transport failure when a caller supplies independent
evidence through :func:`local_transport_failed_outcome`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class PublicationDisposition(str, Enum):
    """Evidence-based outcomes for one DHT publication attempt."""

    ACCEPTED = "accepted"
    SUPERSEDED_EQUIVALENT = "superseded_equivalent"
    CONFLICT = "conflict"
    SHORT_HORIZON = "short_horizon"
    UNVERIFIED = "unverified"
    LOCAL_TRANSPORT_FAILED = "local_transport_failed"


@dataclass(frozen=True)
class PublicationOutcome:
    """Immutable, value-free diagnostic result for one publication key."""

    disposition: PublicationDisposition
    key: str
    subkey: Optional[str]
    store_returned: Optional[bool]
    attempted_expiration: Optional[float]
    minimum_safe_expiration: Optional[float]
    observed_expiration: Optional[float] = None
    observed_equivalent: Optional[bool] = None
    reason: str = ""

    @property
    def publication_safe(self) -> bool:
        return self.disposition in {
            PublicationDisposition.ACCEPTED,
            PublicationDisposition.SUPERSEDED_EQUIVALENT,
        }

    @property
    def permits_transport_recovery(self) -> bool:
        """Only independently constructed transport failures permit recovery."""
        return self.disposition is PublicationDisposition.LOCAL_TRANSPORT_FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return bounded JSON-safe evidence without DHT record contents."""
        return {
            "disposition": self.disposition.value,
            "key": _sanitize_identifier(self.key),
            "subkey": _sanitize_identifier(self.subkey),
            "store_returned": self.store_returned,
            "attempted_expiration": _finite_or_none(self.attempted_expiration),
            "minimum_safe_expiration": _finite_or_none(
                self.minimum_safe_expiration
            ),
            "observed_expiration": _finite_or_none(self.observed_expiration),
            "observed_equivalent": self.observed_equivalent,
            "reason": _sanitize_reason(self.reason),
            "publication_safe": self.publication_safe,
            "transport_recovery_permitted": self.permits_transport_recovery,
        }


def classify_publication(
    *,
    key: str,
    subkey: Optional[str],
    store_returned: Optional[bool],
    expected_value: Any,
    observed_value: Any,
    observed_expiration: Optional[float],
    attempted_expiration: float,
    minimum_safe_expiration: float,
    equivalent: Callable[[Any, Any], bool],
) -> PublicationOutcome:
    """Classify a store result using an independent read-back observation.

    A successful acknowledgement is accepted only when the attempted lease
    reaches the caller's minimum safe horizon.  Every other result needs a
    readable record, a safe observed expiration horizon, and caller-defined
    semantic equivalence.  In particular, ``False`` alone always becomes
    ``unverified`` and never ``local_transport_failed``.
    """
    _validate_identifier("key", key, allow_none=False)
    _validate_identifier("subkey", subkey, allow_none=True)
    attempted = _require_finite("attempted_expiration", attempted_expiration)
    minimum_safe = _require_finite(
        "minimum_safe_expiration", minimum_safe_expiration
    )
    if not callable(equivalent):
        raise TypeError("equivalent must be callable")
    if store_returned not in (True, False, None):
        raise TypeError("store_returned must be True, False, or None")

    if store_returned is True and attempted < minimum_safe:
        return PublicationOutcome(
            disposition=PublicationDisposition.SHORT_HORIZON,
            key=key,
            subkey=subkey,
            store_returned=True,
            attempted_expiration=attempted,
            minimum_safe_expiration=minimum_safe,
            reason="acknowledged_record_expires_too_soon",
        )

    if store_returned is True:
        return PublicationOutcome(
            disposition=PublicationDisposition.ACCEPTED,
            key=key,
            subkey=subkey,
            store_returned=True,
            attempted_expiration=attempted,
            minimum_safe_expiration=minimum_safe,
            reason="store_acknowledged",
        )

    base = {
        "key": key,
        "subkey": subkey,
        "store_returned": store_returned,
        "attempted_expiration": attempted,
        "minimum_safe_expiration": minimum_safe,
    }
    if observed_value is None:
        return PublicationOutcome(
            disposition=PublicationDisposition.UNVERIFIED,
            reason="independent_record_missing",
            **base,
        )

    observed = _finite_or_none(observed_expiration)
    if observed is None:
        return PublicationOutcome(
            disposition=PublicationDisposition.UNVERIFIED,
            observed_expiration=None,
            reason="independent_expiration_missing_or_invalid",
            **base,
        )

    try:
        is_equivalent = bool(equivalent(expected_value, observed_value))
    except Exception:
        return PublicationOutcome(
            disposition=PublicationDisposition.UNVERIFIED,
            observed_expiration=observed,
            reason="equivalence_check_failed",
            **base,
        )

    if not is_equivalent:
        return PublicationOutcome(
            disposition=PublicationDisposition.CONFLICT,
            observed_expiration=observed,
            observed_equivalent=False,
            reason="independent_record_conflicts",
            **base,
        )
    if observed < minimum_safe:
        return PublicationOutcome(
            disposition=PublicationDisposition.SHORT_HORIZON,
            observed_expiration=observed,
            observed_equivalent=True,
            reason="equivalent_record_expires_too_soon",
            **base,
        )
    return PublicationOutcome(
        disposition=PublicationDisposition.SUPERSEDED_EQUIVALENT,
        observed_expiration=observed,
        observed_equivalent=True,
        reason="equivalent_record_has_safe_horizon",
        **base,
    )


def unverified_outcome(
    *,
    key: str,
    subkey: Optional[str],
    attempted_expiration: Optional[float],
    minimum_safe_expiration: Optional[float],
    reason: str,
    store_returned: Optional[bool] = None,
) -> PublicationOutcome:
    """Build an unverified outcome for read/validation failures around the classifier."""
    return PublicationOutcome(
        disposition=PublicationDisposition.UNVERIFIED,
        key=key,
        subkey=subkey,
        store_returned=store_returned,
        attempted_expiration=_finite_or_none(attempted_expiration),
        minimum_safe_expiration=_finite_or_none(minimum_safe_expiration),
        reason=_sanitize_reason(reason),
    )


def local_transport_failed_outcome(
    *,
    key: str,
    subkey: Optional[str],
    attempted_expiration: Optional[float],
    minimum_safe_expiration: Optional[float],
    reason: str,
) -> PublicationOutcome:
    """Build the sole recovery-permitting result from independent evidence."""
    return PublicationOutcome(
        disposition=PublicationDisposition.LOCAL_TRANSPORT_FAILED,
        key=key,
        subkey=subkey,
        store_returned=None,
        attempted_expiration=_finite_or_none(attempted_expiration),
        minimum_safe_expiration=_finite_or_none(minimum_safe_expiration),
        reason=_sanitize_reason(reason),
    )


def _validate_identifier(name: str, value: Optional[str], *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value:
        suffix = " or None" if allow_none else ""
        raise ValueError(f"{name} must be a non-empty string{suffix}")


def _require_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _finite_or_none(value: Optional[float]) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _sanitize_identifier(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    printable = "".join(character for character in str(value) if character.isprintable())
    return printable[:160]


def _sanitize_reason(reason: str) -> str:
    printable = "".join(character for character in str(reason) if character.isprintable())
    return printable[:160]
