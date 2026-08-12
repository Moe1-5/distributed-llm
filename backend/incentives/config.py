"""Environment-backed configuration for useful-work incentives."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast

IncentivesMode = Literal["off", "shadow", "credit"]


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

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


def get_incentives_config() -> IncentivesConfig:
    revision = os.environ.get("DISTRIBLLM_MODEL_REVISION", "main").strip()
    if not revision:
        raise ValueError("DISTRIBLLM_MODEL_REVISION must not be empty")
    return IncentivesConfig(
        mode=parse_incentives_mode(
            os.environ.get("DISTRIBLLM_INCENTIVES_MODE", "off")
        ),
        settlement_url=os.environ.get("DISTRIBLLM_SETTLEMENT_URL", "").strip().rstrip("/"),
        model_revision=revision,
    )
