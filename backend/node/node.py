"""
node.py
Represents this machine as a fully functional P2P serving node.

Startup sequence:
    1. Select verified direct transport or circuit-relay fallback
    2. Start the Hivemind DHT peer
    3. Load transformer layers via InferenceHandler
    4. Start the RPC server
    5. Announce model and transport state on a bounded heartbeat
"""

import time
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional
from uuid import uuid4

import hivemind
import torch
from hivemind.utils import get_dht_time
from hivemind.utils.logging import get_logger

from constants import (
    ANNOUNCE_INTERVAL,
    DHT_EXPIRY_TIME,
    DHT_OPERATION_TIMEOUT,
    DHT_RECOVERY_COOLDOWN_SECONDS,
    DHT_RECOVERY_FAILURE_THRESHOLD,
    P2PNetworkConfig,
    get_p2p_network_config,
)
from node.handler import InferenceHandler
from node.reachability import ReachabilityProtocol, check_direct_reachability
from node.relay_compat import install_static_relay_compat
from node.rpc_server import DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, RPCServer, _run_with_timeout

logger = get_logger(__name__)


class Node:
    def __init__(
        self,
        model_name:    str,
        layer_start:   int,
        layer_end:     int,
        dht_prefix:    str,
        initial_peers: Optional[list[str]] = None,
        device:        str = "cuda",
        dtype:         torch.dtype = torch.float16,
        hf_token:      Optional[str] = None,
        local_model_path: Optional[str] = None,
        node_id:       Optional[str] = None,
        rpc_uid_suffix: Optional[int] = None,
        p2p_config: Optional[P2PNetworkConfig] = None,
    ):
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if layer_end <= layer_start:
            raise ValueError(
                f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
            )
        if not dht_prefix.strip():
            raise ValueError("dht_prefix must not be empty")
        if device not in ("cuda", "cpu"):
            raise ValueError(f"device must be 'cuda' or 'cpu', got '{device}'")

        if device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available — falling back to CPU")
            device = "cpu"
            dtype  = torch.float32

        self.model_name    = model_name
        self.layer_start   = layer_start
        self.layer_end     = layer_end
        self.dht_prefix    = dht_prefix
        self.initial_peers = initial_peers or []
        self.device        = device
        self.dtype         = dtype
        self.hf_token      = hf_token
        self.local_model_path = local_model_path
        self.node_id       = node_id or uuid4().hex[:12]
        self.rpc_uid_suffix = rpc_uid_suffix
        self.p2p_config = p2p_config or get_p2p_network_config()

        self.dht:     Optional[hivemind.DHT]     = None
        self.handler: Optional[InferenceHandler] = None
        self.rpc:     Optional[RPCServer]        = None
        self.reachability_protocol: Optional[ReachabilityProtocol] = None

        self._running         = False
        self._announce_enabled = False
        self._announce_stop = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None
        self._announce_lock = threading.Lock()
        self._last_announce_attempt_at: Optional[float] = None
        self._last_announce_success_at: Optional[float] = None
        self._last_announce_error: Optional[str] = None
        self._consecutive_announce_failures = 0
        self._network_recovery_lock = threading.Lock()
        self._last_network_recovery_at: Optional[float] = None
        self._last_network_recovery_error: Optional[str] = None
        self._network_recovery_count = 0
        self._last_peer_id: Optional[str] = None
        self._last_maddrs: list[str] = []
        self.connection_mode = "checking"
        self.direct_reachability: Optional[bool] = None
        self.transport_verified = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info(
            f"Node starting | model={self.model_name} "
            f"layers={self.layer_start}-{self.layer_end} device={self.device}"
        )
        self.transport_verified = False

        # Step 1: DHT
        if self.dht is None:
            self.connection_mode = self._select_connection_mode()
            if self.connection_mode == "relay":
                install_static_relay_compat()
            logger.info(
                "Step 1/4: Starting DHT in %s mode...",
                self.connection_mode,
            )
            announce_maddrs = list(self.p2p_config.announce_maddrs) or None
            trusted_relays = list(self.p2p_config.trusted_relays) or None
            self.dht = hivemind.DHT(
                host_maddrs=self.p2p_config.host_maddrs,
                announce_maddrs=announce_maddrs,
                initial_peers=self.initial_peers,
                start=True,
                use_ipfs=False,
                auto_nat=self.p2p_config.auto_nat,
                nat_port_map=self.p2p_config.nat_port_map,
                use_relay=True,
                use_auto_relay=(
                    self.p2p_config.use_auto_relay
                    and self.connection_mode == "relay"
                ),
                trusted_relays=trusted_relays,
                client_mode=self.connection_mode == "relay",
                force_reachability=(
                    "private" if self.connection_mode == "relay" else None
                ),
            )
            logger.info(
                "DHT transport args | host_maddrs=%s announce_maddrs=%s "
                "initial_peers=%s auto_nat=%s nat_port_map=%s "
                "use_auto_relay=%s trusted_relays=%s client_mode=%s "
                "force_reachability=%s",
                self.p2p_config.host_maddrs,
                announce_maddrs,
                self.initial_peers,
                self.p2p_config.auto_nat,
                self.p2p_config.nat_port_map,
                self.p2p_config.use_auto_relay
                and self.connection_mode == "relay",
                trusted_relays,
                self.connection_mode == "relay",
                "private" if self.connection_mode == "relay" else None,
            )
        else:
            logger.info("Step 1/4: Reusing existing DHT...")
        if self.dht.peer_id is None:
            raise RuntimeError("DHT started but peer_id is None")
        self._last_peer_id = str(self.dht.peer_id)
        logger.info(f"DHT started. Peer ID: {self.dht.peer_id}")
        if self.connection_mode == "relay":
            self._wait_for_relay_address()
        else:
            self.transport_verified = self.direct_reachability is True
            self._start_reachability_protocol()

        # Step 2: Load layers
        if self.handler is None or not self.handler.is_loaded():
            logger.info("Step 2/4: Loading transformer layers...")
            self.handler = InferenceHandler(
                model_name=self.model_name,
                layer_start=self.layer_start,
                layer_end=self.layer_end,
                device=self.device,
                dtype=self.dtype,
                hf_token=self.hf_token,
                local_model_path=self.local_model_path,
            )
            self.handler.load()
        else:
            logger.info("Step 2/4: Reusing loaded transformer layers...")
        if not self.handler.is_loaded():
            raise RuntimeError("handler.load() completed but is_loaded() is False")

        # Step 3: RPC server
        logger.info("Step 3/4: Starting RPC server...")
        if self.rpc is None:
            self.rpc = RPCServer(
                handler=self.handler,
                dht=self.dht,
                dht_prefix=self.dht_prefix,
                uid_suffix=self.rpc_uid_suffix,
            )
        self.rpc.require_remote_publication = self._remote_lease_required()
        self.rpc.start()
        if not self.rpc.is_running():
            raise RuntimeError("rpc.start() completed but is_running() is False")

        # Step 4: Announce
        logger.info("Step 4/4: Announcing to DHT...")
        self._running = True
        self._announce_enabled = True
        self._announce_stop.clear()
        self._announce()
        self._ensure_announce_thread()

        logger.info(f"Node fully started. Addresses: {self.get_visible_maddrs()}")

    def _select_connection_mode(self) -> str:
        configured_mode = self.p2p_config.mode
        if configured_mode in {"direct", "relay"}:
            if configured_mode == "relay" and not self.initial_peers:
                raise RuntimeError(
                    "Relay mode requires at least one bootstrap or relay peer"
                )
            if (
                configured_mode == "relay"
                and not self.p2p_config.use_auto_relay
            ):
                raise RuntimeError(
                    "Relay mode requires DISTRIBLLM_AUTO_RELAY=true"
                )
            self.direct_reachability = (
                None if configured_mode == "direct" else False
            )
            return configured_mode

        if not self.initial_peers:
            logger.info(
                "No bootstrap peers are configured; using direct mode "
                "for local development"
            )
            self.direct_reachability = None
            return "direct"

        try:
            self.direct_reachability = check_direct_reachability(
                initial_peers=self.initial_peers,
                host_maddrs=self.p2p_config.host_maddrs,
                announce_maddrs=(
                    list(self.p2p_config.announce_maddrs) or None
                ),
                use_ipfs=False,
                use_relay=False,
                auto_nat=self.p2p_config.auto_nat,
                nat_port_map=self.p2p_config.nat_port_map,
                startup_timeout=60,
            )
        except Exception as e:
            logger.warning(
                "Direct reachability probe failed; falling back to relay: %s",
                e,
            )
            self.direct_reachability = None

        if self.direct_reachability is True:
            return "direct"
        if not self.p2p_config.use_auto_relay:
            raise RuntimeError(
                "This worker is not directly reachable and automatic relay "
                "fallback is disabled"
            )
        return "relay"

    def _wait_for_relay_address(self) -> None:
        timeout = self.p2p_config.relay_wait_timeout
        deadline = time.monotonic() + timeout
        last_logged_addresses: Optional[list[str]] = None
        while True:
            addresses = self.get_visible_maddrs(refresh=True)
            if addresses != last_logged_addresses:
                logger.info(
                    "Waiting for relay reservation | peer_id=%s visible_maddrs=%s",
                    self.get_peer_id(),
                    addresses,
                )
                last_logged_addresses = list(addresses)
            if any("/p2p-circuit" in address for address in addresses):
                self.transport_verified = True
                logger.info("Relay reservation ready: %s", addresses)
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        raise RuntimeError(
            "Relay mode was selected, but no p2p-circuit address became "
            f"available within {timeout:g} seconds. Check that the VPS is "
            "relay-capable and reachable, run "
            "`python -m relay_probe --json` from the backend environment, "
            "or increase "
            "DISTRIBLLM_RELAY_WAIT_TIMEOUT."
        )

    def _start_reachability_protocol(self) -> None:
        if (
            self.dht is None
            or self.reachability_protocol is not None
            or not self.initial_peers
        ):
            return
        try:
            self.reachability_protocol = ReachabilityProtocol.attach_to_dht(
                self.dht
            )
        except Exception as e:
            logger.warning(
                "Could not start the optional reachability service: %s",
                e,
            )

    def _stop_reachability_protocol(self) -> None:
        if self.reachability_protocol is not None:
            self.reachability_protocol.shutdown()
            self.reachability_protocol = None

    def turn_off(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        logger.info("Node turning off serving while keeping loaded layers...")
        self._running = False
        self._announce_enabled = False
        self._announce_stop.set()
        if self._announce_thread is not None:
            self._announce_thread.join(timeout=min(timeout, DHT_OPERATION_TIMEOUT + 0.5))
            self._announce_thread = None
        try:
            self._announce(running=False, rpc_running=False)
        except Exception as e:
            logger.warning("Offline announce failed before RPC shutdown: %s", e)
        if self.rpc is not None:
            self.rpc.stop(timeout=timeout)
            self.rpc = None
        self._stop_reachability_protocol()
        if self.dht is not None:
            dht = self.dht
            _run_with_timeout("node-dht-pause-shutdown", dht.shutdown, timeout)
        self.dht = None
        self.transport_verified = False
        logger.info(
            "Node serving turned off; loaded layers are preserved and serving handles were released."
        )

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        logger.info("Node stopping...")
        self._running = False
        self._announce_enabled = False
        self._announce_stop.set()
        if self._announce_thread is not None:
            self._announce_thread.join(timeout=min(timeout, DHT_OPERATION_TIMEOUT + 0.5))
            self._announce_thread = None
        if self.rpc is not None:
            self.rpc.stop(timeout=timeout)
            self.rpc = None
        self._stop_reachability_protocol()
        if self.handler is not None:
            self.handler.unload()
            self.handler = None
        if self.dht is not None:
            dht = self.dht
            _run_with_timeout("node-dht-shutdown", dht.shutdown, timeout)
            self.dht = None
        self.transport_verified = False
        logger.info("Node stopped.")

    # ------------------------------------------------------------------
    # DHT Announcement
    # ------------------------------------------------------------------

    def _announce(
        self,
        running: Optional[bool] = None,
        rpc_running: Optional[bool] = None,
    ) -> None:
        """
        Write node metadata to DHT under two keys:
            1. {prefix}.node_info.{peer_id}  — full metadata
            2. {prefix}.members.v2           — independently leased peer entries

        When bootstrap peers are configured, every authoritative write excludes
        this worker itself. This prevents a local cache-only write from being
        reported as a healthy network announcement when remote replicas are no
        longer reachable. Isolated local development remains self-contained.
        """
        with self._announce_lock:
            if self.dht is None:
                return
            self._last_announce_attempt_at = time.time()
            try:
                peer_id = self.get_peer_id()
                if peer_id is None:
                    raise RuntimeError("DHT peer ID is unavailable")
                expiry = get_dht_time() + DHT_EXPIRY_TIME

                publication_refresher = getattr(
                    self.rpc,
                    "refresh_publication",
                    None,
                )
                should_refresh_publication = bool(
                    self._running
                    and running is not False
                    and rpc_running is not False
                    and callable(publication_refresher)
                )
                publication_error: Optional[Exception] = None
                if should_refresh_publication:
                    try:
                        publication_refresher(expiry)
                    except Exception as exc:
                        # Still refresh node metadata below so peers can see the
                        # publication failure instead of retaining stale state.
                        publication_error = exc
                publication_getter = getattr(
                    self.rpc,
                    "get_publication_status",
                    None,
                )
                rpc_publication = (
                    publication_getter()
                    if callable(publication_getter)
                    else None
                )

                capability_getter = getattr(self.rpc, "get_receipt_capability", None)
                receipt_capability = (
                    capability_getter(peer_id) if callable(capability_getter) else None
                )
                safety_getter = getattr(self.rpc, "get_safety_snapshot", None)
                rpc_safety = safety_getter() if callable(safety_getter) else None
                node_stored = self._dht_call(
                    "node metadata store",
                    self.dht.store,
                    key=f"{self.dht_prefix}.node_info.{peer_id}",
                    value={
                        "peer_id": peer_id,
                        "node_id": self.node_id,
                        "model_name": self.model_name,
                        "layer_start": self.layer_start,
                        "layer_end": self.layer_end,
                        "device": self.device,
                        "running": (
                            self._components_running()
                            if running is None
                            else running
                        ),
                        "maddrs": self.get_visible_maddrs(),
                        "layers_loaded": (
                            self.handler.is_loaded() if self.handler else False
                        ),
                        "rpc_running": (
                            self.rpc.is_running() if self.rpc else False
                        ) if rpc_running is None else rpc_running,
                        "rpc_uid": self.rpc.get_uid() if self.rpc else None,
                        "connection_mode": self.connection_mode,
                        "direct_reachability": self.direct_reachability,
                        "transport_verified": self.transport_verified,
                        "loading": getattr(self.handler, "load_diagnostics", None),
                        "rpc_safety": rpc_safety,
                        "rpc_publication": rpc_publication,
                        "timestamp": time.time(),
                        **(receipt_capability or {}),
                    },
                    expiration_time=expiry,
                    exclude_self=self._remote_lease_required(),
                )
                if node_stored is False:
                    raise RuntimeError(
                        "Remote DHT peers rejected the node metadata refresh"
                    )

                members_v2_key = f"{self.dht_prefix}.members.v2"
                members_stored = self._dht_call(
                    "member lease refresh",
                    self.dht.store,
                    key=members_v2_key,
                    subkey=peer_id,
                    value={
                        "peer_id": peer_id,
                        "node_id": self.node_id,
                        "timestamp": time.time(),
                    },
                    expiration_time=expiry,
                    exclude_self=self._remote_lease_required(),
                )
                if members_stored is False:
                    raise RuntimeError(
                        "Remote DHT peers rejected the member lease refresh"
                    )

                # Keep the legacy aggregate index during the transition. It is
                # best-effort because concurrent read-modify-write updates can
                # legitimately supersede one another. New clients use v2.
                members_key = f"{self.dht_prefix}.members"
                try:
                    result = self._dht_call(
                        "legacy members lookup",
                        self.dht.get,
                        members_key,
                        latest=True,
                    )
                    existing = (
                        result.value
                        if result and isinstance(result.value, list)
                        else []
                    )
                    members = list(dict.fromkeys([*existing, peer_id]))
                    self._dht_call(
                        "legacy members refresh",
                        self.dht.store,
                        key=members_key,
                        value=members,
                        expiration_time=expiry,
                        exclude_self=self._remote_lease_required(),
                    )
                except Exception as exc:
                    logger.debug("Legacy members refresh failed: %s", exc)

                self._last_announce_success_at = time.time()
                self._last_announce_error = None
                self._consecutive_announce_failures = 0
                logger.debug(
                    "Announced to DHT | layers=%s-%s rpc_publication_fresh=%s "
                    "hivemind_publisher_alive=%s",
                    self.layer_start,
                    self.layer_end,
                    (
                        rpc_publication.get("fresh")
                        if isinstance(rpc_publication, dict)
                        else None
                    ),
                    (
                        rpc_publication.get("hivemind_publisher_alive")
                        if isinstance(rpc_publication, dict)
                        else None
                    ),
                )
                if publication_error is not None:
                    raise RuntimeError(
                        "RPC expert publication refresh failed: "
                        f"{publication_error}"
                    ) from publication_error
            except Exception as exc:
                self._last_announce_error = str(exc)
                self._consecutive_announce_failures += 1
                raise

    @staticmethod
    def _dht_call(name: str, method, *args, **kwargs):
        try:
            future = method(*args, return_future=True, **kwargs)
        except TypeError as exc:
            if "return_future" not in str(exc):
                raise
            return method(*args, **kwargs)
        try:
            return future.result(timeout=DHT_OPERATION_TIMEOUT)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"DHT {name} exceeded {DHT_OPERATION_TIMEOUT:g} seconds"
            ) from exc

    def _remote_lease_required(self) -> bool:
        """Require remote acknowledgement outside isolated local development."""
        return bool(self.initial_peers)

    def _ensure_announce_thread(self) -> None:
        if (
            not self._announce_enabled
            or (
                self._announce_thread is not None
                and self._announce_thread.is_alive()
            )
        ):
            return
        self._announce_thread = threading.Thread(
            target=self._announce_loop,
            daemon=True,
            name="node-announce",
        )
        self._announce_thread.start()

    def _announce_loop(self) -> None:
        while self._announce_enabled and not self._announce_stop.wait(
            ANNOUNCE_INTERVAL
        ):
            try:
                self._announce()
            except Exception as e:
                logger.warning("Re-announce failed: %s", e)
                if self._should_recover_network():
                    self._recover_network_transport()

    def _should_recover_network(self) -> bool:
        if (
            not self._announce_enabled
            or self._consecutive_announce_failures
            < DHT_RECOVERY_FAILURE_THRESHOLD
        ):
            return False
        if self._last_network_recovery_at is None:
            return True
        return (
            time.time() - self._last_network_recovery_at
            >= DHT_RECOVERY_COOLDOWN_SECONDS
        )

    def _recover_network_transport(self) -> None:
        """Restart DHT and RPC handles while preserving loaded model layers."""
        if not self._network_recovery_lock.acquire(blocking=False):
            return
        try:
            if not self._announce_enabled:
                return
            self._last_network_recovery_at = time.time()
            self._network_recovery_count += 1
            self._last_network_recovery_error = None
            logger.warning(
                "Recovering worker DHT/RPC transport after %s failed remote "
                "lease refreshes; loaded layers will be preserved",
                self._consecutive_announce_failures,
            )
            self._running = False
            if self.rpc is not None:
                self.rpc.stop()
                self.rpc = None
            self._stop_reachability_protocol()
            if self.dht is not None:
                dht = self.dht
                _run_with_timeout(
                    "node-dht-recovery-shutdown",
                    dht.shutdown,
                    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
                )
                self.dht = None
            self.transport_verified = False
            if not self._announce_enabled:
                logger.info("Worker network recovery cancelled by lifecycle stop")
                return
            self.start()
            logger.info("Worker DHT/RPC transport recovery completed")
        except Exception as exc:
            self._last_network_recovery_error = f"{type(exc).__name__}: {exc}"
            self._last_announce_error = (
                "Network transport recovery failed: "
                f"{self._last_network_recovery_error}"
            )
            logger.error(
                "Worker DHT/RPC transport recovery failed: %s",
                exc,
                exc_info=True,
            )
        finally:
            self._network_recovery_lock.release()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _components_running(self) -> bool:
        components_running = (
            self._running
            and self.dht     is not None
            and self.handler is not None and self.handler.is_loaded()
            and self.rpc     is not None and self.rpc.is_running()
        )
        if not components_running:
            return False
        publication_getter = getattr(
            self.rpc,
            "get_publication_status",
            None,
        )
        if not callable(publication_getter):
            return True
        return bool(publication_getter().get("fresh"))

    def is_running(self) -> bool:
        announcement = self.get_announcement_status()
        return bool(
            self._components_running()
            and announcement["thread_alive"]
            and announcement["fresh"]
        )

    def get_announcement_status(self) -> dict:
        success_age = (
            max(0.0, time.time() - self._last_announce_success_at)
            if self._last_announce_success_at is not None
            else None
        )
        return {
            "thread_alive": bool(
                self._announce_thread is not None and self._announce_thread.is_alive()
            ),
            "last_attempt_at": self._last_announce_attempt_at,
            "last_success_at": self._last_announce_success_at,
            "success_age_seconds": success_age,
            "last_error": self._last_announce_error,
            "consecutive_failures": self._consecutive_announce_failures,
            "network_recovery_count": self._network_recovery_count,
            "last_network_recovery_at": self._last_network_recovery_at,
            "last_network_recovery_error": self._last_network_recovery_error,
            "fresh": bool(
                self._last_announce_success_at is not None
                and success_age is not None
                and success_age < DHT_EXPIRY_TIME
            ),
        }

    def get_info(self) -> dict:
        peer_id = self.get_peer_id()
        capability_getter = getattr(self.rpc, "get_receipt_capability", None)
        receipt_capability = (
            capability_getter(peer_id)
            if callable(capability_getter) and peer_id is not None
            else None
        )
        safety_getter = getattr(self.rpc, "get_safety_snapshot", None)
        rpc_safety = safety_getter() if callable(safety_getter) else None
        publication_getter = getattr(
            self.rpc,
            "get_publication_status",
            None,
        )
        rpc_publication = (
            publication_getter()
            if callable(publication_getter)
            else None
        )
        return {
            "peer_id":       peer_id,
            "node_id":       self.node_id,
            "rpc_uid":       self.rpc.get_uid() if self.rpc else None,
            "model_name":    self.model_name,
            "layer_start":   self.layer_start,
            "layer_end":     self.layer_end,
            "device":        self.device,
            "running":       self.is_running(),
            "maddrs":        self.get_visible_maddrs(),
            "layers_loaded": self.handler.is_loaded() if self.handler else False,
            "rpc_running":   self.rpc.is_running()    if self.rpc     else False,
            "connection_mode": self.connection_mode,
            "direct_reachability": self.direct_reachability,
            "transport_verified": self.transport_verified,
            "loading":       getattr(self.handler, "load_diagnostics", None),
            "rpc_safety":    rpc_safety,
            "rpc_publication": rpc_publication,
            "announcement":  self.get_announcement_status(),
            "accounting":    self.get_accounting_snapshot(),
            **(receipt_capability or {}),
        }

    def get_accounting_snapshot(self) -> dict:
        base = {
            "peer_id": self.get_peer_id(),
            "model_name": self.model_name,
            "layer_start": self.layer_start,
            "layer_end": self.layer_end,
            "layers_served": self.layer_end - self.layer_start,
            "device": self.device,
            "requests_served": 0,
            "failed_requests": 0,
            "token_positions_served": 0,
            "total_latency_ms": 0.0,
            "avg_latency_ms": 0.0,
            "last_success_at": None,
            "last_error_at": None,
        }
        if self.handler is None:
            return base
        return {
            **base,
            **self.handler.get_accounting_snapshot(),
        }

    def get_visible_maddrs(self, *, refresh: bool = False) -> list[str]:
        if self.dht is None:
            return self._last_maddrs
        try:
            addresses = (
                self.dht.get_visible_maddrs(latest=True)
                if refresh
                else self.dht.get_visible_maddrs()
            )
            self._last_maddrs = [str(addr) for addr in addresses]
            return self._last_maddrs
        except Exception as e:
            logger.warning("Failed to read DHT visible addresses: %s", e)
            return self._last_maddrs

    def get_peer_id(self) -> Optional[str]:
        if self.dht is None:
            return self._last_peer_id
        try:
            self._last_peer_id = str(self.dht.peer_id) if self.dht.peer_id else None
            return self._last_peer_id
        except Exception as e:
            logger.warning("Failed to read DHT peer id: %s", e)
            return self._last_peer_id

    def __repr__(self) -> str:
        return (
            f"Node(model={self.model_name}, "
            f"layers={self.layer_start}-{self.layer_end}, "
            f"running={self.is_running()})"
        )
