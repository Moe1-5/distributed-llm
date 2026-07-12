"""
settings.py
Persistent backend settings stored in a local file.

Currently manages:
    - HuggingFace token (hf_xxxx / hf_oauth_xxxx) for downloading gated models

Token file location: $DISTRIBLLM_HF_TOKEN_FILE, or
    $XDG_CONFIG_HOME/distribllm/hf_token, or ~/.config/distribllm/hf_token
    - Plain text, single line, written by manual fallback or OAuth device login
    - Gitignored — never committed
    - Survives backend restarts
"""

import os
from pathlib import Path
from hivemind.utils.logging import get_logger

from api.env_loader import load_project_env

load_project_env()

logger = get_logger(__name__)

_TOKEN_FILE_ENV = "DISTRIBLLM_HF_TOKEN_FILE"


def _default_token_file() -> Path:
    config_root = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    return config_root / "distribllm" / "hf_token"


def _token_file() -> Path:
    configured = os.environ.get(_TOKEN_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_token_file()


def get_hf_token() -> str | None:
    """
    Read the stored HuggingFace token from disk.
    Returns None if not set.
    """
    token_file = _token_file()
    try:
        if not token_file.exists():
            return None
        token = token_file.read_text().strip()
        if not token:
            return None
        assert token.startswith("hf_"), (
            f"Token file exists but value does not start with 'hf_' — "
            f"may be corrupted. Delete {token_file} and re-enter your token."
        )
        return token
    except AssertionError:
        raise
    except Exception as e:
        logger.warning(f"Failed to read HF token from {token_file}: {e}")
        return None


def save_hf_token(token: str) -> None:
    """
    Save a HuggingFace token to disk.
    Validates format before saving.
    """
    assert token and token.strip(), "Token must not be empty"
    token = token.strip()
    assert token.startswith("hf_"), (
        f"Invalid token format — HuggingFace tokens start with 'hf_', got: '{token[:8]}...'"
    )

    token_file = _token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(token_file.parent, 0o700)
    token_file.write_text(token)
    # Restrict file permissions — token is sensitive
    os.chmod(token_file, 0o600)
    logger.info(f"HF token saved to {token_file}")


def delete_hf_token() -> None:
    """Remove the stored token."""
    token_file = _token_file()
    if token_file.exists():
        token_file.unlink()
        logger.info("HF token deleted.")


def token_is_set() -> bool:
    """Quick check — returns True if a valid token is stored."""
    try:
        return get_hf_token() is not None
    except Exception:
        return False
