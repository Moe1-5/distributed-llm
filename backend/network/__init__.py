"""Backend-owned network lifecycle and publication primitives."""

from .publication import (
    PublicationDisposition,
    PublicationOutcome,
    classify_publication,
    local_transport_failed_outcome,
    unverified_outcome,
)

__all__ = [
    "PublicationDisposition",
    "PublicationOutcome",
    "classify_publication",
    "local_transport_failed_outcome",
    "unverified_outcome",
]
