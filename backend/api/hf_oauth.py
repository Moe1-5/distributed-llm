"""
Hugging Face OAuth/device-login helpers.

The renderer never receives the OAuth access token. It only receives a short
flow id, the Hugging Face verification URL, and the user code to authorize.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from errno import ENOSPC
from threading import Lock, Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import (
    GatedRepoError,
    HFValidationError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)
from hivemind.utils.logging import get_logger

from api.env_loader import load_project_env
from api.local_models import LocalModelValidationError, import_local_model
from api.settings import delete_hf_token, get_hf_token, save_hf_token
from constants import SUPPORTED_MODELS

load_project_env()

logger = get_logger(__name__)

HF_DEVICE_ENDPOINT = "https://huggingface.co/oauth/device"
HF_TOKEN_ENDPOINT = "https://huggingface.co/oauth/token"
HF_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
HF_OAUTH_SCOPE = "openid profile gated-repos"
HF_OAUTH_CLIENT_ID_ENV = "DISTRIBLLM_HF_OAUTH_CLIENT_ID"


class HuggingFaceOAuthError(RuntimeError):
    """Raised when Hugging Face OAuth/download fails in an actionable way."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class DeviceFlow:
    flow_id: str
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_at: float
    interval: int
    client_id: str
    scope: str


_device_flows: dict[str, DeviceFlow] = {}
_download_jobs: dict[str, dict[str, Any]] = {}
_download_jobs_lock = Lock()


def _oauth_client_id() -> str | None:
    value = os.environ.get(HF_OAUTH_CLIENT_ID_ENV, "").strip()
    return value or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redacted_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "model_name": job["model_name"],
        "revision": job.get("revision"),
        "status": job["status"],
        "message": job.get("message"),
        "error": job.get("error"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "completed_at": job.get("completed_at"),
        "model": job.get("model"),
    }


def _set_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with _download_jobs_lock:
        job = _download_jobs[job_id]
        job.update(updates)
        job["updated_at"] = _now_iso()
        return dict(job)


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
        return dict(job) if job is not None else None


def _configured_or_raise() -> str:
    client_id = _oauth_client_id()
    if not client_id:
        raise HuggingFaceOAuthError(
            "hf_oauth_not_configured",
            (
                "Hugging Face OAuth is not configured. Set "
                f"{HF_OAUTH_CLIENT_ID_ENV} to a public Hugging Face OAuth app client ID."
            ),
            status_code=503,
        )
    return client_id


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": "hf_oauth_http_error", "error_description": raw}
        raise HuggingFaceOAuthError(
            str(payload.get("error") or "hf_oauth_http_error"),
            str(payload.get("error_description") or payload.get("error") or exc),
            status_code=exc.code,
        ) from exc
    except URLError as exc:
        raise HuggingFaceOAuthError(
            "hf_oauth_network_error",
            f"Could not reach Hugging Face OAuth: {exc.reason}",
            status_code=503,
        ) from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HuggingFaceOAuthError(
            "hf_oauth_invalid_response",
            "Hugging Face OAuth returned invalid JSON.",
            status_code=502,
        ) from exc
    if not isinstance(result, dict):
        raise HuggingFaceOAuthError(
            "hf_oauth_invalid_response",
            "Hugging Face OAuth returned an unexpected response.",
            status_code=502,
        )
    return result


def _public_connection(token: str | None) -> dict[str, Any]:
    if not token:
        return {
            "configured": _oauth_client_id() is not None,
            "connected": False,
            "username": None,
            "token_preview": None,
            "scope": HF_OAUTH_SCOPE,
            "client_id_set": _oauth_client_id() is not None,
        }

    username = None
    try:
        whoami = HfApi().whoami(token=token, cache=False)
        username = whoami.get("name") or whoami.get("fullname")
    except Exception as exc:
        logger.warning("Failed to read Hugging Face connected user: %s", exc)

    return {
        "configured": _oauth_client_id() is not None,
        "connected": True,
        "username": username,
        "token_preview": f"{token[:8]}...",
        "scope": HF_OAUTH_SCOPE,
        "client_id_set": _oauth_client_id() is not None,
    }


def get_huggingface_connection() -> dict[str, Any]:
    return _public_connection(get_hf_token())


def start_huggingface_device_flow() -> dict[str, Any]:
    client_id = _configured_or_raise()
    payload = _post_form(
        HF_DEVICE_ENDPOINT,
        {
            "client_id": client_id,
            "scope": HF_OAUTH_SCOPE,
        },
    )
    required = ["device_code", "user_code", "verification_uri"]
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise HuggingFaceOAuthError(
            "hf_oauth_invalid_response",
            f"Hugging Face device flow response is missing: {', '.join(missing)}.",
            status_code=502,
        )

    expires_in = int(payload.get("expires_in") or 900)
    interval = max(2, int(payload.get("interval") or 5))
    flow_id = uuid.uuid4().hex
    flow = DeviceFlow(
        flow_id=flow_id,
        device_code=str(payload["device_code"]),
        user_code=str(payload["user_code"]),
        verification_uri=str(payload["verification_uri"]),
        verification_uri_complete=(
            str(payload["verification_uri_complete"])
            if payload.get("verification_uri_complete")
            else None
        ),
        expires_at=time.time() + expires_in,
        interval=interval,
        client_id=client_id,
        scope=HF_OAUTH_SCOPE,
    )
    _device_flows[flow_id] = flow
    return {
        "flow_id": flow.flow_id,
        "user_code": flow.user_code,
        "verification_uri": flow.verification_uri,
        "verification_uri_complete": flow.verification_uri_complete,
        "expires_in": expires_in,
        "interval": flow.interval,
        "scope": flow.scope,
    }


def poll_huggingface_device_flow(flow_id: str) -> dict[str, Any]:
    flow = _device_flows.get(flow_id)
    if flow is None:
        raise HuggingFaceOAuthError(
            "hf_oauth_flow_not_found",
            "This Hugging Face login flow was not found. Start a new connection.",
            status_code=404,
        )
    if time.time() >= flow.expires_at:
        _device_flows.pop(flow_id, None)
        raise HuggingFaceOAuthError(
            "expired_token",
            "This Hugging Face login code expired. Start a new connection.",
            status_code=408,
        )

    try:
        payload = _post_form(
            HF_TOKEN_ENDPOINT,
            {
                "grant_type": HF_DEVICE_GRANT,
                "device_code": flow.device_code,
                "client_id": flow.client_id,
            },
        )
    except HuggingFaceOAuthError as exc:
        if exc.code in {"authorization_pending", "slow_down"}:
            return {
                "status": "pending",
                "error": exc.code,
                "message": exc.message,
                "interval": flow.interval + (2 if exc.code == "slow_down" else 0),
            }
        if exc.code in {"access_denied", "expired_token"}:
            _device_flows.pop(flow_id, None)
        raise

    token = str(payload.get("access_token") or "").strip()
    if not token.startswith("hf_"):
        raise HuggingFaceOAuthError(
            "hf_oauth_missing_access_token",
            "Hugging Face did not return a usable access token.",
            status_code=502,
        )

    save_hf_token(token)
    _device_flows.pop(flow_id, None)
    connection = _public_connection(token)
    return {
        "status": "connected",
        "connection": connection,
    }


def disconnect_huggingface() -> dict[str, Any]:
    delete_hf_token()
    _device_flows.clear()
    return {"status": "disconnected", "connection": _public_connection(None)}


def download_huggingface_model(model_name: str, revision: str | None = None) -> dict[str, Any]:
    model_name = model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise HuggingFaceOAuthError(
            "unsupported_model",
            f"Unsupported model '{model_name}'.",
            status_code=400,
        )

    token = get_hf_token()
    if SUPPORTED_MODELS[model_name]["gated"] and not token:
        raise HuggingFaceOAuthError(
            "hf_not_connected",
            "Connect Hugging Face before downloading a gated model.",
            status_code=401,
        )

    try:
        snapshot_path = snapshot_download(
            repo_id=model_name,
            revision=revision,
            token=token,
            repo_type="model",
        )
        imported = import_local_model(model_name, snapshot_path)
    except GatedRepoError as exc:
        raise HuggingFaceOAuthError(
            "gated_model_access_denied",
            (
                f"The connected Hugging Face account is not approved for {model_name}. "
                "Request or accept access on Hugging Face, then try again."
            ),
            status_code=403,
        ) from exc
    except RepositoryNotFoundError as exc:
        raise HuggingFaceOAuthError(
            "hf_model_not_found_or_private",
            f"Hugging Face could not find {model_name} for the connected account.",
            status_code=404,
        ) from exc
    except HFValidationError as exc:
        raise HuggingFaceOAuthError(
            "hf_download_invalid_request",
            str(exc),
            status_code=400,
        ) from exc
    except HfHubHTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in (401, 403):
            raise HuggingFaceOAuthError(
                "hf_token_invalid_or_unauthorized",
                "Hugging Face rejected the saved authorization. Disconnect and reconnect.",
                status_code=403,
            ) from exc
        raise HuggingFaceOAuthError(
            "hf_download_failed",
            f"Hugging Face download failed. Hub status: {status_code or 'unknown'}.",
            status_code=502,
        ) from exc
    except LocalModelValidationError as exc:
        raise HuggingFaceOAuthError(
            exc.code,
            exc.message,
            status_code=400,
        ) from exc
    except OSError as exc:
        if exc.errno == ENOSPC:
            total, used, free = shutil.disk_usage(os.getcwd())
            raise HuggingFaceOAuthError(
                "insufficient_disk_space",
                (
                    "Not enough disk space to download this model. "
                    f"Free space: {free // (1024 ** 3)} GB."
                ),
                status_code=507,
            ) from exc
        raise HuggingFaceOAuthError(
            "hf_download_filesystem_error",
            f"Could not write downloaded model files: {exc}",
            status_code=500,
        ) from exc

    return {
        "status": "downloaded",
        "model": imported,
        "message": f"Downloaded and imported {model_name}.",
    }


def start_huggingface_model_download(
    model_name: str,
    revision: str | None = None,
) -> dict[str, Any]:
    model_name = model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise HuggingFaceOAuthError(
            "unsupported_model",
            f"Unsupported model '{model_name}'.",
            status_code=400,
        )
    if SUPPORTED_MODELS[model_name]["gated"] and not get_hf_token():
        raise HuggingFaceOAuthError(
            "hf_not_connected",
            "Connect Hugging Face before downloading a gated model.",
            status_code=401,
        )

    job_id = uuid.uuid4().hex
    created_at = _now_iso()
    job = {
        "job_id": job_id,
        "model_name": model_name,
        "revision": revision,
        "status": "queued",
        "message": "Download queued.",
        "error": None,
        "cancel_requested": False,
        "created_at": created_at,
        "updated_at": created_at,
        "completed_at": None,
        "model": None,
    }
    with _download_jobs_lock:
        _download_jobs[job_id] = job

    thread = Thread(
        target=_run_download_job,
        args=(job_id,),
        name=f"hf-download-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"status": "started", "job": _redacted_job(job)}


def _run_download_job(job_id: str) -> None:
    job = _get_job(job_id)
    if job is None:
        return
    if job.get("cancel_requested"):
        _set_job(
            job_id,
            status="cancelled",
            message="Download cancelled before it started.",
            completed_at=_now_iso(),
        )
        return

    _set_job(job_id, status="downloading", message="Downloading from Hugging Face.")
    try:
        result = download_huggingface_model(
            str(job["model_name"]),
            job.get("revision"),
        )
    except HuggingFaceOAuthError as exc:
        _set_job(
            job_id,
            status="failed",
            error=exc.code,
            message=exc.message,
            completed_at=_now_iso(),
        )
        return
    except Exception as exc:
        logger.error("Unhandled Hugging Face download job failure: %s", exc, exc_info=True)
        _set_job(
            job_id,
            status="failed",
            error="hf_download_failed",
            message=f"Download failed: {exc}",
            completed_at=_now_iso(),
        )
        return

    latest = _get_job(job_id)
    if latest and latest.get("cancel_requested"):
        _set_job(
            job_id,
            status="cancelled",
            message=(
                "Cancel was requested after the Hugging Face transfer completed. "
                "The validated local import remains available and can be removed from Network."
            ),
            model=result.get("model"),
            completed_at=_now_iso(),
        )
        return

    _set_job(
        job_id,
        status="completed",
        message=result.get("message", "Downloaded and imported model."),
        model=result.get("model"),
        completed_at=_now_iso(),
    )


def get_huggingface_download_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if job is None:
        raise HuggingFaceOAuthError(
            "download_job_not_found",
            "Download job was not found.",
            status_code=404,
        )
    return {"job": _redacted_job(job)}


def cancel_huggingface_download_job(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if job is None:
        raise HuggingFaceOAuthError(
            "download_job_not_found",
            "Download job was not found.",
            status_code=404,
        )
    if job["status"] in {"completed", "failed", "cancelled"}:
        return {"job": _redacted_job(job)}
    updated = _set_job(
        job_id,
        cancel_requested=True,
        message="Cancel requested. Waiting for the current Hugging Face transfer step to stop.",
    )
    return {"job": _redacted_job(updated)}
