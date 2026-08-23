"""Environment-backed configuration for useful-work incentives."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal, cast

IncentivesMode = Literal["off", "shadow", "credit"]
IMMUTABLE_MODEL_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def is_immutable_model_revision(value: object) -> bool:
    """Return whether value is a canonical full Git object ID."""
    return (
        isinstance(value, str)
        and IMMUTABLE_MODEL_REVISION_PATTERN.fullmatch(value) is not None
    )


def parse_incentives_mode(value: str) -> IncentivesMode:
    normalized = value.strip().lower()
    if normalized not in {"off", "shadow", "credit"}:
        raise ValueError("DISTRIBLLM_INCENTIVES_MODE must be off, shadow, or credit")
    return cast(IncentivesMode, normalized)


@dataclass(frozen=True)
class IncentivesConfig:
    mode: IncentivesMode
    settlement_url: str
    model_revision: str

    def __post_init__(self) -> None:
        if (
            self.enabled
            and self.model_revision
            and not is_immutable_model_revision(self.model_revision)
        ):
            raise ValueError(
                "DISTRIBLLM_MODEL_REVISION must be an exact lowercase 40-character "
                "commit hash when incentives are enabled"
            )

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


def get_incentives_config() -> IncentivesConfig:
    revision = os.environ.get("DISTRIBLLM_MODEL_REVISION", "").strip()
    return IncentivesConfig(
        mode=parse_incentives_mode(
            os.environ.get("DISTRIBLLM_INCENTIVES_MODE", "off")
        ),
        settlement_url=os.environ.get("DISTRIBLLM_SETTLEMENT_URL", "").strip().rstrip("/"),
        model_revision=revision,
    )
