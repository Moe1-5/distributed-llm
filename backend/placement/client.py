"""Authenticated backend client and heartbeat owner for placement leases."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse


class PlacementClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})

    @property
    def transient(self) -> bool:
        return self.status_code is None or self.status_code >= 500


@dataclass(frozen=True)
class PlacementConfig:
    base_url: Optional[str]
    auth_token: Optional[str]
    model_revision: str
    request_timeout_seconds: float = 8.0
    heartbeat_interval_seconds: float = 20.0

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    @classmethod
    def from_env(cls) -> "PlacementConfig":
        raw_url = os.environ.get("DISTRIBLLM_PLACEMENT_URL", "").strip()
        auth_token = os.environ.get("DISTRIBLLM_PLACEMENT_AUTH_TOKEN", "").strip()
        model_revision = os.environ.get(
            "DISTRIBLLM_PLACEMENT_MODEL_REVISION",
            "main",
        ).strip()
        request_timeout = _finite_float_env(
            "DISTRIBLLM_PLACEMENT_REQUEST_TIMEOUT_SECONDS",
            8.0,
            minimum=0.1,
        )
        heartbeat_interval = _finite_float_env(
            "DISTRIBLLM_PLACEMENT_HEARTBEAT_INTERVAL_SECONDS",
            20.0,
            minimum=1.0,
        )
        if not raw_url:
            return cls(
                base_url=None,
                auth_token=None,
                model_revision=model_revision or "main",
                request_timeout_seconds=request_timeout,
                heartbeat_interval_seconds=heartbeat_interval,
            )
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DISTRIBLLM_PLACEMENT_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "DISTRIBLLM_PLACEMENT_URL must not contain credentials, a query, or a fragment"
            )
        if len(auth_token) < 32:
            raise ValueError(
                "DISTRIBLLM_PLACEMENT_AUTH_TOKEN must contain at least 32 characters "
                "when placement coordination is enabled"
            )
        if not model_revision:
            raise ValueError("DISTRIBLLM_PLACEMENT_MODEL_REVISION must not be blank")
        return cls(
            base_url=raw_url.rstrip("/"),
            auth_token=auth_token,
            model_revision=model_revision,
            request_timeout_seconds=request_timeout,
            heartbeat_interval_seconds=heartbeat_interval,
        )


def _finite_float_env(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number at least {minimum}")
    return value


class PlacementCoordinatorClient:
    def __init__(
        self,
        config: PlacementConfig,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not config.enabled:
            raise ValueError("PlacementCoordinatorClient requires an enabled config")
        self.config = config
        self._opener = opener

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.auth_token}",
        }
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.config.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(
                request,
                timeout=self.config.request_timeout_seconds,
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            document = _decode_document(exc.read())
            detail = document.get("detail", document)
            if not isinstance(detail, dict):
                detail = {"message": str(detail)}
            raise PlacementClientError(
                str(detail.get("error", "placement_http_error")),
                str(detail.get("message", f"Placement service returned HTTP {exc.code}")),
                status_code=int(exc.code),
                details=detail,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PlacementClientError(
                "placement_unavailable",
                f"Placement coordinator is unavailable: {exc}",
            ) from exc
        return _decode_document(raw)

    def plan(self, model_name: str, layer_capacity: int) -> dict[str, Any]:
        path = (
            f"/v1/models/{quote(model_name, safe='')}/plan"
            f"?model_revision={quote(self.config.model_revision, safe='')}"
            f"&layer_capacity={int(layer_capacity)}"
        )
        return self._request("GET", path)

    def reserve(
        self,
        *,
        participant_id: str,
        idempotency_key: str,
        model_name: str,
        layer_capacity: int,
        placement_mode: str,
        layer_start: Optional[int],
        layer_end: Optional[int],
        expected_topology_revision: Optional[int],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/reservations",
            {
                "participant_id": participant_id,
                "idempotency_key": idempotency_key,
                "model_name": model_name,
                "model_revision": self.config.model_revision,
                "layer_capacity": int(layer_capacity),
                "placement_mode": placement_mode,
                "layer_start": layer_start,
                "layer_end": layer_end,
                "expected_topology_revision": expected_topology_revision,
            },
        )

    def joining(self, reservation: dict[str, Any], node_id: str) -> dict[str, Any]:
        return self._mutation(reservation, "joining", {"node_id": node_id})

    def online(
        self,
        reservation: dict[str, Any],
        *,
        node_id: str,
        peer_id: str,
        rpc_uid: str,
        advertised_peer_id: str,
        advertised_rpc_uid: str,
        model_revision: str,
        advertised_model_revision: str,
        rpc_ready: bool,
        publication_ready: bool,
    ) -> dict[str, Any]:
        return self._mutation(
            reservation,
            "online",
            {
                "node_id": node_id,
                "peer_id": peer_id,
                "rpc_uid": rpc_uid,
                "advertised_peer_id": advertised_peer_id,
                "advertised_rpc_uid": advertised_rpc_uid,
                "model_revision": model_revision,
                "advertised_model_revision": advertised_model_revision,
                "rpc_ready": rpc_ready,
                "publication_ready": publication_ready,
            },
        )

    def renew(self, reservation: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if reservation.get("state") == "ONLINE":
            payload.update(
                peer_id=reservation.get("peer_id"),
                rpc_uid=reservation.get("rpc_uid"),
            )
        return self._mutation(reservation, "renew", payload)

    def release(self, reservation: dict[str, Any], reason: str) -> dict[str, Any]:
        return self._mutation(reservation, "release", {"reason": reason})

    def _mutation(
        self,
        reservation: dict[str, Any],
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        reservation_id = str(reservation["reservation_id"])
        return self._request(
            "POST",
            f"/v1/reservations/{quote(reservation_id, safe='')}/{action}",
            {
                "participant_id": reservation["participant_id"],
                "reservation_token": reservation["reservation_token"],
                **payload,
            },
        )


def _decode_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlacementClientError(
            "placement_response_invalid",
            "Placement coordinator returned invalid JSON.",
        ) from exc
    if not isinstance(value, dict):
        raise PlacementClientError(
            "placement_response_invalid",
            "Placement coordinator returned a non-object JSON response.",
        )
    return value


class PlacementRuntime:
    """Owns local lease secrets and renews active provider reservations."""

    def __init__(
        self,
        config: Optional[PlacementConfig] = None,
        *,
        client: Optional[PlacementCoordinatorClient] = None,
    ) -> None:
        self.config = config or PlacementConfig.from_env()
        self.client = (
            client
            if client is not None
            else PlacementCoordinatorClient(self.config)
            if self.config.enabled
            else None
        )
        self._lock = threading.RLock()
        self._leases: dict[str, dict[str, Any]] = {}
        self._last_error: Optional[str] = None
        self._connectivity = "disabled" if self.client is None else "idle"
        self._last_success_at: Optional[float] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lease_lost_callback: Optional[
            Callable[[str, PlacementClientError], None]
        ] = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def plan(self, model_name: str, layer_capacity: int) -> dict[str, Any]:
        if self.client is None:
            raise PlacementClientError(
                "placement_disabled",
                "Placement coordination is not configured.",
            )
        return self._call(self.client.plan, model_name, layer_capacity)

    def reserve(self, **kwargs: Any) -> dict[str, Any]:
        if self.client is None:
            raise PlacementClientError(
                "placement_disabled",
                "Placement coordination is not configured.",
            )
        result = self._call(self.client.reserve, **kwargs)
        return {
            **result,
            "reservation": _validated_reservation_response(
                result,
                require_token=True,
                expected_state="RESERVED",
            ),
        }

    def begin_joining(
        self,
        reservation: dict[str, Any],
        node_id: str,
    ) -> dict[str, Any]:
        if self.client is None:
            raise PlacementClientError("placement_disabled", "Placement is disabled.")
        result = self._call(self.client.joining, reservation, node_id)
        tracked = _validated_reservation_response(
            result,
            previous=reservation,
            require_token=True,
            expected_state="JOINING",
        )
        tracked = self._with_local_deadline(tracked)
        with self._lock:
            self._leases[node_id] = tracked
            self._ensure_thread_locked()
        self._wake.set()
        return self.public_lease(node_id) or {}

    def mark_online(self, node_id: str, node_info: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise PlacementClientError("placement_disabled", "Placement is disabled.")
        with self._lock:
            reservation = dict(self._leases[node_id])
        announcement = node_info.get("announcement") or {}
        rpc_publication = node_info.get("rpc_publication") or {}
        peer_id = str(node_info.get("peer_id") or "")
        rpc_uid = str(node_info.get("rpc_uid") or "")
        published_uids = {
            str(uid) for uid in rpc_publication.get("uids", []) if str(uid).strip()
        }
        advertised_rpc_uid = rpc_uid if rpc_uid in published_uids else ""
        model_revision = str(node_info.get("placement_model_revision") or "")
        result = self._call(
            self.client.online,
            reservation,
            node_id=node_id,
            peer_id=peer_id,
            rpc_uid=rpc_uid,
            advertised_peer_id=str(node_info.get("rpc_peer_id") or ""),
            advertised_rpc_uid=advertised_rpc_uid,
            model_revision=model_revision,
            advertised_model_revision=model_revision,
            rpc_ready=bool(node_info.get("rpc_running")),
            publication_ready=bool(
                announcement.get("fresh")
                and rpc_publication.get("fresh")
                and advertised_rpc_uid == rpc_uid
            ),
        )
        tracked = _validated_reservation_response(
            result,
            previous=reservation,
            require_token=True,
            expected_state="ONLINE",
        )
        tracked = self._with_local_deadline(tracked)
        with self._lock:
            self._leases[node_id] = tracked
        return self.public_lease(node_id) or {}

    def release_node(self, node_id: str, reason: str) -> Optional[dict[str, Any]]:
        with self._lock:
            reservation = self._leases.get(node_id)
        if reservation is None:
            return None
        result: Optional[dict[str, Any]] = None
        try:
            if self.client is None:
                raise PlacementClientError(
                    "placement_disabled",
                    "Placement coordination is not configured.",
                )
            result = self._call(self.client.release, dict(reservation), reason)
        finally:
            with self._lock:
                self._leases.pop(node_id, None)
        return result

    def release_reservation(
        self,
        reservation: Optional[dict[str, Any]],
        reason: str,
    ) -> Optional[dict[str, Any]]:
        if reservation is None or self.client is None:
            return None
        return self._call(self.client.release, dict(reservation), reason)

    def public_lease(self, node_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            reservation = self._leases.get(node_id)
            if reservation is None:
                return None
            return _public_reservation(reservation)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "connectivity": self._connectivity,
                "model_revision": self.config.model_revision,
                "heartbeat_interval_seconds": self.config.heartbeat_interval_seconds,
                "active_leases": [
                    _public_reservation(lease) for lease in self._leases.values()
                ],
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
                "heartbeat_running": bool(
                    self._thread is not None and self._thread.is_alive()
                ),
            }

    def set_lease_lost_callback(
        self,
        callback: Optional[Callable[[str, PlacementClientError], None]],
    ) -> None:
        with self._lock:
            self._lease_lost_callback = callback

    def shutdown(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
        return not bool(thread is not None and thread.is_alive())

    def _call(
        self,
        target: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            result = target(*args, **kwargs)
        except PlacementClientError as exc:
            with self._lock:
                self._connectivity = "unavailable" if exc.transient else "rejected"
                self._last_error = str(exc)
            raise
        with self._lock:
            self._connectivity = "connected"
            self._last_error = None
            self._last_success_at = time.time()
        return result

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="placement-heartbeat",
        )
        self._thread.start()

    def _with_local_deadline(self, reservation: dict[str, Any]) -> dict[str, Any]:
        """Stop locally before the coordinator can reallocate an expired lease."""
        tracked = dict(reservation)
        try:
            valid_for = float(tracked["expires_at"]) - float(tracked["updated_at"])
        except (KeyError, TypeError, ValueError):
            return tracked
        if not math.isfinite(valid_for) or valid_for <= 0:
            tracked["_local_deadline_monotonic"] = time.monotonic()
            return tracked
        safety_margin = min(
            self.config.heartbeat_interval_seconds,
            valid_for / 2,
        )
        tracked["_local_deadline_monotonic"] = (
            time.monotonic() + valid_for - safety_margin
        )
        return tracked

    def _lose_lease(
        self,
        node_id: str,
        reservation: dict[str, Any],
        error: PlacementClientError,
    ) -> None:
        callback = None
        with self._lock:
            current = self._leases.get(node_id)
            if (
                current is not None
                and current.get("reservation_id") == reservation.get("reservation_id")
            ):
                self._leases.pop(node_id, None)
                callback = self._lease_lost_callback
        if callback is not None:
            try:
                callback(node_id, error)
            except Exception as callback_exc:
                with self._lock:
                    self._last_error = (
                        f"{error}; lease-loss cleanup failed: {callback_exc}"
                    )

    def _heartbeat_loop(self) -> None:
        interval = self.config.heartbeat_interval_seconds
        while not self._stop.is_set():
            with self._lock:
                deadlines = [
                    float(lease["_local_deadline_monotonic"])
                    for lease in self._leases.values()
                    if "_local_deadline_monotonic" in lease
                ]
            wait_seconds = interval
            if deadlines:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, min(deadlines) - time.monotonic()),
                )
            self._wake.wait(wait_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                leases = list(self._leases.items())
            for node_id, reservation in leases:
                if self._stop.is_set():
                    break
                local_deadline = reservation.get("_local_deadline_monotonic")
                if (
                    local_deadline is not None
                    and time.monotonic() >= float(local_deadline)
                ):
                    self._lose_lease(
                        node_id,
                        reservation,
                        PlacementClientError(
                            "placement_lease_deadline_elapsed",
                            "The local placement safety deadline elapsed before renewal.",
                            status_code=409,
                        ),
                    )
                    continue
                try:
                    if self.client is None:
                        raise PlacementClientError(
                            "placement_disabled",
                            "Placement coordination is not configured.",
                            status_code=409,
                        )
                    result = self._call(self.client.renew, dict(reservation))
                except PlacementClientError as exc:
                    if not exc.transient:
                        self._lose_lease(node_id, reservation, exc)
                    elif (
                        local_deadline is not None
                        and time.monotonic() >= float(local_deadline)
                    ):
                        self._lose_lease(
                            node_id,
                            reservation,
                            PlacementClientError(
                                "placement_lease_deadline_elapsed",
                                "The local placement safety deadline elapsed during a coordinator outage.",
                                status_code=409,
                            ),
                        )
                    continue
                updated = {
                    **_validated_reservation_response(
                        result,
                        previous=reservation,
                        require_token=True,
                    )
                }
                updated = self._with_local_deadline(updated)
                with self._lock:
                    current = self._leases.get(node_id)
                    if current is not None and current.get("reservation_id") == reservation.get(
                        "reservation_id"
                    ):
                        self._leases[node_id] = updated


def _public_reservation(reservation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in reservation.items()
        if key not in {"reservation_token", "idempotency_key"}
        and not key.startswith("_")
    }


def _validated_reservation_response(
    result: dict[str, Any],
    *,
    previous: Optional[dict[str, Any]] = None,
    require_token: bool = False,
    expected_state: Optional[str] = None,
) -> dict[str, Any]:
    document = result.get("reservation")
    if not isinstance(document, dict):
        raise PlacementClientError(
            "placement_response_invalid",
            "Placement coordinator response has no reservation object.",
            status_code=502,
        )
    merged = {**(previous or {}), **document}
    for field in ("reservation_id", "participant_id", "state"):
        if not str(merged.get(field) or "").strip():
            raise PlacementClientError(
                "placement_response_invalid",
                f"Placement coordinator reservation is missing {field}.",
                status_code=502,
            )
    if require_token and not str(merged.get("reservation_token") or "").strip():
        raise PlacementClientError(
            "placement_response_invalid",
            "Placement coordinator reservation is missing its private token.",
            status_code=502,
        )
    if expected_state is not None and merged["state"] != expected_state:
        raise PlacementClientError(
            "placement_response_invalid",
            f"Placement coordinator returned state {merged['state']!r}; expected {expected_state}.",
            status_code=502,
        )
    for field in ("updated_at", "expires_at"):
        if field not in merged:
            continue
        try:
            value = float(merged[field])
        except (TypeError, ValueError) as exc:
            raise PlacementClientError(
                "placement_response_invalid",
                f"Placement coordinator reservation has invalid {field}.",
                status_code=502,
            ) from exc
        if not math.isfinite(value):
            raise PlacementClientError(
                "placement_response_invalid",
                f"Placement coordinator reservation has non-finite {field}.",
                status_code=502,
            )
    return merged


def create_placement_runtime_from_env() -> PlacementRuntime:
    return PlacementRuntime(PlacementConfig.from_env())
