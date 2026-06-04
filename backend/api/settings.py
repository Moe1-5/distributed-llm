"""
settings.py
Persistent backend settings stored in a local file.

Currently manages:
    - HuggingFace token (hf_xxxx) for downloading gated models

Token file location: {backend_root}/.hf_token
    - Plain text, single line
    - Gitignored — never committed
    - Survives backend restarts
"""

import os
from pathlib import Path
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)

# Token file sits at the backend root (same dir as main.py)
_TOKEN_FILE = Path(__file__).parent.parent / ".hf_token"


def get_hf_token() -> str | None:
    """
    Read the stored HuggingFace token from disk.
    Returns None if not set.
    """
    try:
        if not _TOKEN_FILE.exists():
            return None
        token = _TOKEN_FILE.read_text().strip()
        if not token:
            return None
        assert token.startswith("hf_"), (
            f"Token file exists but value does not start with 'hf_' — "
            f"may be corrupted. Delete {_TOKEN_FILE} and re-enter your token."
        )
        return token
    except AssertionError:
        raise
    except Exception as e:
        logger.warning(f"Failed to read HF token from {_TOKEN_FILE}: {e}")
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

    _TOKEN_FILE.write_text(token)
    # Restrict file permissions — token is sensitive
    os.chmod(_TOKEN_FILE, 0o600)
    logger.info(f"HF token saved to {_TOKEN_FILE}")


def delete_hf_token() -> None:
    """Remove the stored token."""
    if _TOKEN_FILE.exists():
        _TOKEN_FILE.unlink()
        logger.info("HF token deleted.")


def token_is_set() -> bool:
    """Quick check — returns True if a valid token is stored."""
    try:
        return get_hf_token() is not None
    except Exception:
        return False
