"""Backend operator environment loader.

Supports packaged XDG configuration and checkout-local development without
adding a dependency just for simple KEY=VALUE parsing.
"""

from __future__ import annotations

import os
from pathlib import Path


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_project_env() -> Path | None:
    configured = os.environ.get("DISTRIBLLM_ENV_FILE", "").strip()
    config_root = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [
            config_root / "distribllm" / "backend.env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
    )
    env_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if env_path is None:
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, _unquote(value.strip()))

    return env_path
