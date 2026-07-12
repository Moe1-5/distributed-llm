"""
Project environment loader.

Keeps backend local development aligned with the root .env file without adding
an extra dependency just for simple KEY=VALUE parsing.
"""

from __future__ import annotations

import os
from pathlib import Path


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_project_env() -> Path | None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
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
