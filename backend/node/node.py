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
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
import re
from typing import Any, Optional
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
    EXPERT_RPC_UID_SCHEMA_VERSION,
    P2PNetworkConfig,
    get_p2p_identity_dir,
    get_p2p_network_config,
)
from node.handler import InferenceHandler
from node.reachability import ReachabilityProtocol, check_direct_reachability
from node.relay_compat import install_static_relay_compat
from node.rpc_server import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    RPCServer,
    _BorrowedDHT,
)
from network.publication import (
    PublicationOutcome,
    classify_publication,
    local_transport_failed_outcome,
    unverified_outcome,
)

logger = get_logger(__name__)

PublicationVerifier = Callable[..., PublicationOutcome]

_NODE_PUBLICATION_FIELDS = (
    "peer_id",
    "node_id",
    "model_name",
    "layer_start",
    "layer_end",
    "device",
    "running",
    "layers_loaded",
    "rpc_running",
    "rpc_uid",
    "rpc_uid_schema_version",
    "rpc_peer_id",
    "receipt_rpc_uid",
    "application_public_key",
    "model_revision",
    "placement_model_revision",
)


def _node_publication_equivalent(expected: Any, observed: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    return all(expected.get(field) == observed.get(field) for field in _NODE_PUBLICATION_FIELDS)


def _member_publication_equivalent(expected: Any, observed: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    return (
        expected.get("peer_id") == observed.get("peer_id")
        and expected.get("node_id") == observed.get("node_id")
    )


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
        placement_model_revision: Optional[str] = None,
        p2p_config: Optional[P2PNetworkConfig] = None,
        p2p_identity_dir: Optional[Path | str] = None,
        publication_verifier: Optional[PublicationVerifier] = None,
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
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.node_id):
            raise ValueError(
                "node_id must contain only letters, numbers, dots, underscores, "
                "or hyphens and must not exceed 128 characters"
            )
        self.rpc_uid_suffix = rpc_uid_suffix
        self.placement_model_revision = placement_model_revision
        self.placement_lease: Optional[dict[str, Any]] = None
        self.p2p_config = p2p_config or get_p2p_network_config()
        self.p2p_identity_dir = (
            Path(p2p_identity_dir).expanduser()
            if p2p_identity_dir is not None
            else get_p2p_identity_dir()
        )
        self._p2p_identity_path = (
            self.p2p_identity_dir / f"{self.node_id}.key"
        )
        self.publication_verifier = publication_verifier
        # All DHT command-pipe access stays in this process. RPC connection
        # handler children receive a captured P2P daemon address instead of
        # borrowing the pipe, so a force-killed child cannot poison this lock.
        self._dht_command_lock = threading.RLock()

        self.dht:     Optional[hivemind.DHT]     = None
        self.handler: Optional[InferenceHandler] = None
        self.rpc:     Optional[RPCServer]        = None
        self.reachability_protocol: Optional[ReachabilityProtocol] = None

        self._offline_publish_thread: Optional[threading.Thread] = None
        self._offline_publish_error: Optional[BaseException] = None
        self._offline_published = False
        self._dht_shutdown_thread: Optional[threading.Thread] = None
        self._dht_shutdown_target: Optional[hivemind.DHT] = None
        self._dht_shutdown_error: Optional[BaseException] = None

        self._running         = False
        self._announce_enabled = False
        self._announce_stop = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None
        self._announce_lock = threading.Lock()
        self._last_announce_attempt_at: Optional[float] = None
        self._last_announce_success_at: Optional[float] = None
        self._last_announce_error: Optional[str] = None
        self._consecutive_announce_failures = 0
        self._consecutive_publication_failures = 0
        self._publication_outcomes: dict[str, PublicationOutcome] = {}
        self._verified_transport_failure: Optional[PublicationOutcome] = None
        self._lifecycle_lock = threading.RLock()
        self._network_recovery_lock = threading.Lock()
        self._last_network_recovery_at: Optional[float] = None
        self._last_network_recovery_error: Optional[str] = None
        self._network_recovery_count = 0
        self._last_network_recovery_trigger: Optional[str] = None
        self._last_network_recovery_failure_count = 0
        self._last_network_recovery_peer_id_before: Optional[str] = None
        self._last_network_recovery_peer_id_after: Optional[str] = None
        self._last_network_recovery_identity_preserved: Optional[bool] = None
        self._last_peer_id: Optional[str] = None
        self._last_maddrs: list[str] = []
        self.connection_mode = "checking"
        self.direct_reachability: Optional[bool] = None
        self.transport_verified = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, *, expected_peer_id: Optional[str] = None) -> None:
        """Start or resume this node under its single lifecycle owner."""
        with self._lifecycle_lock:
            self._start_owned(expected_peer_id=expected_peer_id)

    def _start_owned(self, *, expected_peer_id: Optional[str] = None) -> None:
        logger.info(
            f"Node starting | model={self.model_name} "
            f"layers={self.layer_start}-{self.layer_end} device={self.device}"
        )
        if not self._running and self.reachability_protocol is not None:
            if not self._stop_reachability_protocol(timeout=0.0):
                raise RuntimeError(
                    "The previous reachability service shutdown is still in "
                    "progress; retry after cleanup completes."
                )
        if not self._running and self.rpc is not None:
            if self.rpc.stop(timeout=0.0) is False:
                raise RuntimeError(
                    "The previous worker RPC shutdown is still in progress; "
                    "retry after cleanup completes."
                )
            self.rpc = None
        self.transport_verified = False

        # Step 1: DHT
        if (
            self._dht_shutdown_thread is not None
            or self._dht_shutdown_target is not None
        ):
            if not self._shutdown_dht_runtime(
                "node-dht-prior-shutdown",
                timeout=0.0,
            ):
                raise RuntimeError(
                    "The previous worker DHT shutdown is still in progress; "
                    "retry after cleanup completes."
                )
        if self.dht is None:
            required_peer_id = expected_peer_id or self._last_peer_id
            self.connection_mode = self._select_connection_mode()
            if self.connection_mode == "relay":
                install_static_relay_compat()
            logger.info(
                "Step 1/4: Starting DHT in %s mode...",
                self.connection_mode,
            )
            announce_maddrs = list(self.p2p_config.announce_maddrs) or None
            trusted_relays = list(self.p2p_config.trusted_relays) or None
            identity_path = self._prepare_p2p_identity_path()
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
                identity_path=str(identity_path),
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
        with self._dht_command_lock:
            dht_peer_id = self.dht.peer_id
        if dht_peer_id is None:
            raise RuntimeError("DHT started but peer_id is None")
        peer_id = str(dht_peer_id)
        required_peer_id = expected_peer_id or self._last_peer_id
        if required_peer_id is not None and peer_id != required_peer_id:
            shutdown_complete = self._shutdown_dht_runtime(
                "node-dht-identity-mismatch-shutdown",
                DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
            )
            raise RuntimeError(
                "Worker P2P identity changed while restarting transport: "
                f"expected {required_peer_id}, got {peer_id}. Refusing to "
                "publish a replacement peer for the loaded worker."
                + (
                    " Cleanup of the mismatched transport is still pending."
                    if not shutdown_complete
                    else ""
                )
            )
        self._last_peer_id = peer_id
        if self._p2p_identity_path.is_file():
            self._p2p_identity_path.chmod(0o600)
        logger.info("DHT started. Peer ID: %s", peer_id)
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
                dht_command_lock=self._dht_command_lock,
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
        self._offline_published = False
        self._announce()
        self._ensure_announce_thread()

        logger.info(f"Node fully started. Addresses: {self.get_visible_maddrs()}")

    def _prepare_p2p_identity_path(self) -> Path:
        """Create a private key directory and validate the worker key target."""
        self.p2p_identity_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.p2p_identity_dir.is_dir():
            raise RuntimeError(
                "DISTRIBLLM_P2P_IDENTITY_DIR must point to a directory"
            )
        self.p2p_identity_dir.chmod(0o700)
        if self._p2p_identity_path.exists():
            if not self._p2p_identity_path.is_file():
                raise RuntimeError(
                    "The configured worker P2P identity target is not a file"
                )
            self._p2p_identity_path.chmod(0o600)
        return self._p2p_identity_path

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
                _BorrowedDHT(self.dht, self._dht_command_lock)
            )
        except Exception as e:
            logger.warning(
                "Could not start the optional reachability service: %s",
                e,
            )

    def _stop_reachability_protocol(self, timeout: float) -> bool:
        protocol = self.reachability_protocol
        if protocol is None:
            return True
        if not protocol.shutdown(timeout=max(0.0, timeout)):
            logger.warning(
                "Reachability service shutdown is still in progress; "
                "retaining its exact lifecycle owner"
            )
            return False
        if self.reachability_protocol is protocol:
            self.reachability_protocol = None
        return True

    def _publish_offline_metadata(self, timeout: float) -> bool:
        """Publish one bounded tombstone before releasing network handles."""
        if self.dht is None or self._offline_published:
            return True
        thread = self._offline_publish_thread
        if thread is None:
            self._offline_publish_error = None

            def _publish() -> None:
                try:
                    self._announce(running=False, rpc_running=False)
                except BaseException as exc:
                    self._offline_publish_error = exc

            thread = threading.Thread(
                target=_publish,
                daemon=True,
                name=f"node-offline-publication-{self.node_id}",
            )
            self._offline_publish_thread = thread
            thread.start()
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            logger.warning(
                "Offline publication did not finish within %.3g seconds; "
                "retaining worker transport handles for retry",
                timeout,
            )
            return False
        error = self._offline_publish_error
        self._offline_publish_thread = None
        self._offline_publish_error = None
        if error is not None:
            logger.warning("Offline announce failed before transport shutdown: %s", error)
            return True
        self._offline_published = True
        return True

    def _shutdown_dht_runtime(self, name: str, timeout: float) -> bool:
        """Join or start the one shutdown attempt owned by this worker DHT."""
        dht = self.dht
        target = self._dht_shutdown_target
        thread = self._dht_shutdown_thread
        if dht is None and target is None:
            return True
        if target is not None and dht is not None and target is not dht:
            logger.error("Worker DHT shutdown ownership mismatch; retaining both handles")
            return False
        target = target or dht
        if thread is None:
            self._dht_shutdown_target = target
            self._dht_shutdown_error = None

            def _shutdown() -> None:
                try:
                    with self._dht_command_lock:
                        target.shutdown()
                except BaseException as exc:
                    self._dht_shutdown_error = exc

            thread = threading.Thread(
                target=_shutdown,
                daemon=True,
                name=name,
            )
            self._dht_shutdown_thread = thread
            thread.start()
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            logger.warning(
                "Worker DHT shutdown did not finish within %.3g seconds; "
                "retaining the exact shutdown attempt for retry",
                timeout,
            )
            return False
        error = self._dht_shutdown_error
        if error is not None:
            logger.warning("Worker DHT shutdown failed; retaining handle: %s", error)
            return False
        is_alive = getattr(target, "is_alive", None)
        if not callable(is_alive):
            logger.error(
                "Worker DHT shutdown returned without a process-liveness probe; "
                "retaining the exact handle"
            )
            return False
        try:
            with self._dht_command_lock:
                process_alive = bool(is_alive())
        except BaseException as exc:
            logger.warning(
                "Worker DHT liveness verification failed; retaining handle: %s",
                exc,
            )
            return False
        if process_alive:
            logger.warning(
                "Worker DHT shutdown returned while its process is still alive; "
                "retaining the exact handle for a liveness retry"
            )
            return False
        self._dht_shutdown_thread = None
        self._dht_shutdown_error = None
        self._dht_shutdown_target = None
        if self.dht is target:
            self.dht = None
        return True

    def _requires_turn_off_owned(self) -> bool:
        return bool(
            self._running
            or self._announce_enabled
            or self._announce_thread is not None
            or self._offline_publish_thread is not None
            or self.rpc is not None
            or self.reachability_protocol is not None
            or self.dht is not None
            or self._dht_shutdown_thread is not None
            or self._dht_shutdown_target is not None
        )

    def requires_turn_off(self) -> bool:
        """Atomically report whether Turn Off still has lifecycle work to own.

        A false result also proves recovery cannot start: announcements are
        disabled and no serving transport handle remains under this lock.
        """
        with self._lifecycle_lock:
            return self._requires_turn_off_owned()

    def has_pending_serving_cleanup(self) -> bool:
        """Return whether a non-running node still needs Turn Off cleanup."""
        with self._lifecycle_lock:
            return bool(not self._running and self._requires_turn_off_owned())

    def turn_off(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> bool:
        """Pause serving without racing start, recovery, or destructive stop."""
        with self._lifecycle_lock:
            return self._turn_off_owned(timeout=timeout)

    def _turn_off_owned(
        self,
        timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> bool:
        logger.info("Node turning off serving while keeping loaded layers...")
        deadline = time.monotonic() + max(0.0, timeout)
        self._running = False
        self._announce_enabled = False
        self._announce_stop.set()
        if self._announce_thread is not None:
            self._announce_thread.join(
                timeout=min(
                    max(0.0, deadline - time.monotonic()),
                    DHT_OPERATION_TIMEOUT + 0.5,
                )
            )
            if self._announce_thread.is_alive():
                logger.warning("Node announce loop is still stopping; retaining its handles")
                return False
            self._announce_thread = None
        if not self._publish_offline_metadata(
            max(0.0, deadline - time.monotonic())
        ):
            return False
        if self.rpc is not None:
            stopped = self.rpc.stop(timeout=max(0.0, deadline - time.monotonic()))
            if stopped is False:
                return False
            self.rpc = None
        if not self._stop_reachability_protocol(
            max(0.0, deadline - time.monotonic())
        ):
            return False
        if not self._shutdown_dht_runtime(
            "node-dht-pause-shutdown",
            max(0.0, deadline - time.monotonic()),
        ):
            return False
        self.transport_verified = False
        logger.info(
            "Node serving turned off; loaded layers are preserved and serving handles were released."
        )
        return True

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> bool:
        """Unload this node without overlapping any other lifecycle action."""
        with self._lifecycle_lock:
            return self._stop_owned(timeout=timeout)

    def _stop_owned(
        self,
        timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> bool:
        logger.info("Node stopping...")
        deadline = time.monotonic() + max(0.0, timeout)
        self._running = False
        self._announce_enabled = False
        self._announce_stop.set()
        if self._announce_thread is not None:
            self._announce_thread.join(
                timeout=min(
                    max(0.0, deadline - time.monotonic()),
                    DHT_OPERATION_TIMEOUT + 0.5,
                )
            )
            if self._announce_thread.is_alive():
                logger.warning("Node announce loop is still stopping; retaining its handles")
                return False
            self._announce_thread = None
        if not self._publish_offline_metadata(
            max(0.0, deadline - time.monotonic())
        ):
            return False
        if self.rpc is not None:
            stopped = self.rpc.stop(timeout=max(0.0, deadline - time.monotonic()))
            if stopped is False:
                return False
            self.rpc = None
        if not self._stop_reachability_protocol(
            max(0.0, deadline - time.monotonic())
        ):
            return False
        if self.handler is not None:
            self.handler.unload()
            self.handler = None
        if not self._shutdown_dht_runtime(
            "node-dht-shutdown",
            max(0.0, deadline - time.monotonic()),
        ):
            return False
        self.transport_verified = False
        logger.info("Node stopped.")
        return True

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
                if should_refresh_publication:
                    try:
                        publication_refresher(expiry)
                    except Exception as exc:
                        # Dependency-owned expert publication is diagnostic.
                        # Peer-addressed dispatch uses the selected peer from
                        # project-owned node metadata and never infers local
                        # transport loss from this observation alone.
                        logger.warning(
                            "Hivemind expert publisher observation failed: %s",
                            exc,
                        )
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
                node_key = f"{self.dht_prefix}.node_info.{peer_id}"
                node_value = {
                    "peer_id": peer_id,
                    "node_id": self.node_id,
                    "model_name": self.model_name,
                    "placement_model_revision": self.placement_model_revision,
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
                    "rpc_uid_schema_version": EXPERT_RPC_UID_SCHEMA_VERSION,
                    "rpc_peer_id": peer_id,
                    "connection_mode": self.connection_mode,
                    "direct_reachability": self.direct_reachability,
                    "transport_verified": self.transport_verified,
                    "loading": getattr(self.handler, "load_diagnostics", None),
                    "rpc_safety": rpc_safety,
                    "rpc_publication": rpc_publication,
                    "timestamp": time.time(),
                    **(receipt_capability or {}),
                }
                node_stored = self._dht_call(
                    "node metadata store",
                    self.dht.store,
                    key=node_key,
                    value=node_value,
                    expiration_time=expiry,
                    exclude_self=self._remote_lease_required(),
                )
                node_outcome = self._classify_publication(
                    key=node_key,
                    subkey=None,
                    store_returned=node_stored,
                    expected_value=node_value,
                    attempted_expiration=expiry,
                    equivalent=_node_publication_equivalent,
                )

                members_v2_key = f"{self.dht_prefix}.members.v2"
                member_value = {
                    "peer_id": peer_id,
                    "node_id": self.node_id,
                    "timestamp": time.time(),
                }
                members_stored = self._dht_call(
                    "member lease refresh",
                    self.dht.store,
                    key=members_v2_key,
                    subkey=peer_id,
                    value=member_value,
                    expiration_time=expiry,
                    exclude_self=self._remote_lease_required(),
                )
                member_outcome = self._classify_publication(
                    key=members_v2_key,
                    subkey=peer_id,
                    store_returned=members_stored,
                    expected_value=member_value,
                    attempted_expiration=expiry,
                    equivalent=_member_publication_equivalent,
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

                outcomes = (node_outcome, member_outcome)
                if all(outcome.publication_safe for outcome in outcomes):
                    self._last_announce_success_at = time.time()
                    self._last_announce_error = None
                    self._consecutive_publication_failures = 0
                else:
                    self._consecutive_publication_failures += 1
                    unsafe = ", ".join(
                        outcome.disposition.value
                        for outcome in outcomes
                        if not outcome.publication_safe
                    )
                    self._last_announce_error = (
                        "Publication visibility is degraded: " + unsafe
                    )
                transport_failure = self._transport_failure_outcome()
                if transport_failure is None:
                    self._consecutive_announce_failures = 0
                else:
                    self._consecutive_announce_failures += 1
                    self._last_announce_error = (
                        "Local transport health check failed: "
                        f"{transport_failure.reason}"
                    )
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
            except Exception as exc:
                self._last_announce_error = str(exc)
                self._consecutive_announce_failures += 1
                raise

    def _classify_publication(
        self,
        *,
        key: str,
        subkey: Optional[str],
        store_returned: Optional[bool],
        expected_value: Any,
        attempted_expiration: float,
        equivalent: Callable[[Any, Any], bool],
    ) -> PublicationOutcome:
        verifier = self.publication_verifier
        if verifier is not None:
            try:
                outcome = verifier(
                    key=key,
                    subkey=subkey,
                    store_returned=store_returned,
                    expected_value=expected_value,
                    attempted_expiration=attempted_expiration,
                    equivalent=equivalent,
                )
            except Exception as exc:
                outcome = unverified_outcome(
                    key=key,
                    subkey=subkey,
                    store_returned=store_returned,
                    attempted_expiration=attempted_expiration,
                    minimum_safe_expiration=(
                        get_dht_time()
                        + max(ANNOUNCE_INTERVAL, DHT_OPERATION_TIMEOUT)
                    ),
                    reason=f"verifier_{type(exc).__name__}: {exc}",
                )
        elif store_returned is True:
            outcome = classify_publication(
                key=key,
                subkey=subkey,
                store_returned=True,
                expected_value=expected_value,
                observed_value=None,
                observed_expiration=None,
                attempted_expiration=attempted_expiration,
                minimum_safe_expiration=(
                    get_dht_time() + max(ANNOUNCE_INTERVAL, DHT_OPERATION_TIMEOUT)
                ),
                equivalent=equivalent,
            )
        else:
            outcome = unverified_outcome(
                key=key,
                subkey=subkey,
                store_returned=store_returned,
                attempted_expiration=attempted_expiration,
                minimum_safe_expiration=(
                    get_dht_time() + max(ANNOUNCE_INTERVAL, DHT_OPERATION_TIMEOUT)
                ),
                reason="independent_verifier_unavailable",
            )
        label = key if subkey is None else f"{key}[{subkey}]"
        self._publication_outcomes[label] = outcome
        logger.log(
            20 if outcome.publication_safe else 30,
            "Worker publication classified | key=%s disposition=%s "
            "store_returned=%s recovery_permitted=%s",
            label,
            outcome.disposition.value,
            store_returned,
            outcome.permits_transport_recovery,
        )
        return outcome

    def _dht_call(self, name: str, method, *args, **kwargs):
        with self._dht_command_lock:
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
            self._run_announce_heartbeat()

    def _run_announce_heartbeat(self) -> None:
        """Run one heartbeat and schedule evidence-gated transport repair."""
        try:
            self._announce()
        except Exception as exc:
            logger.warning("Re-announce failed: %s", exc)
        if self._should_recover_network():
            self._recover_network_transport()

    def mark_transport_failure_verified(self, reason: str) -> PublicationOutcome:
        """Accept independent local transport evidence from a supervisor/probe."""
        normalized = reason.strip()
        if not normalized:
            raise ValueError("Verified transport failure reason must not be empty")
        outcome = local_transport_failed_outcome(
            key=f"worker-runtime:{self.node_id}",
            subkey=None,
            attempted_expiration=None,
            minimum_safe_expiration=None,
            reason=normalized,
        )
        self._verified_transport_failure = outcome
        self._consecutive_announce_failures = max(
            self._consecutive_announce_failures,
            DHT_RECOVERY_FAILURE_THRESHOLD,
        )
        return outcome

    def _transport_failure_outcome(self) -> Optional[PublicationOutcome]:
        if (
            self._verified_transport_failure is not None
            and self._verified_transport_failure.permits_transport_recovery
        ):
            return self._verified_transport_failure
        if self._running and self.dht is None:
            return local_transport_failed_outcome(
                key=f"worker-runtime:{self.node_id}",
                subkey=None,
                attempted_expiration=None,
                minimum_safe_expiration=None,
                reason="worker_dht_runtime_missing",
            )
        dht_is_alive = getattr(self.dht, "is_alive", None)
        if self.dht is not None and callable(dht_is_alive):
            try:
                with self._dht_command_lock:
                    dht_alive = bool(dht_is_alive())
                if not dht_alive:
                    return local_transport_failed_outcome(
                        key=f"worker-runtime:{self.node_id}",
                        subkey=None,
                        attempted_expiration=None,
                        minimum_safe_expiration=None,
                        reason="worker_dht_process_not_alive",
                    )
            except Exception:
                pass
        if self._running and (
            self.rpc is None or not self.rpc.is_running()
        ):
            return local_transport_failed_outcome(
                key=f"worker-runtime:{self.node_id}",
                subkey=None,
                attempted_expiration=None,
                minimum_safe_expiration=None,
                reason=(
                    "worker_rpc_runtime_missing"
                    if self.rpc is None
                    else "worker_rpc_runtime_not_alive"
                ),
            )
        return None

    def _should_recover_network(self) -> bool:
        transport_failure = self._transport_failure_outcome()
        if (
            not self._announce_enabled
            or transport_failure is None
            or not transport_failure.permits_transport_recovery
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
        if not self._should_recover_network():
            return
        if not self._network_recovery_lock.acquire(blocking=False):
            return
        if not self._lifecycle_lock.acquire(blocking=False):
            self._network_recovery_lock.release()
            return
        try:
            self._recover_network_transport_owned()
        finally:
            self._lifecycle_lock.release()
            self._network_recovery_lock.release()

    def _recover_network_transport_owned(self) -> None:
        """Perform one evidence-gated repair while owning the node lifecycle."""
        try:
            if not self._announce_enabled:
                return
            transport_failure = self._transport_failure_outcome()
            if (
                transport_failure is None
                or not transport_failure.permits_transport_recovery
            ):
                return
            recovery_trigger = transport_failure.reason
            failure_count = self._consecutive_announce_failures
            previous_peer_id = self.get_peer_id()
            self._last_network_recovery_at = time.time()
            self._network_recovery_count += 1
            self._last_network_recovery_error = None
            self._last_network_recovery_trigger = recovery_trigger
            self._last_network_recovery_failure_count = failure_count
            self._last_network_recovery_peer_id_before = previous_peer_id
            self._last_network_recovery_peer_id_after = None
            self._last_network_recovery_identity_preserved = None
            logger.warning(
                "Recovering worker DHT/RPC transport after %s failed remote "
                "lease refreshes; loaded layers will be preserved",
                failure_count,
            )
            self._running = False
            if self.rpc is not None:
                if self.rpc.stop() is False:
                    raise RuntimeError(
                        "Worker RPC shutdown is still in progress; retaining "
                        "the transport for the next recovery attempt"
                    )
                self.rpc = None
            if not self._stop_reachability_protocol(
                DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
            ):
                raise RuntimeError(
                    "Reachability service shutdown is still in progress; "
                    "retaining the worker transport for the next recovery "
                    "attempt"
                )
            if not self._shutdown_dht_runtime(
                "node-dht-recovery-shutdown",
                DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
            ):
                raise RuntimeError(
                    "Worker DHT shutdown is still in progress; retaining "
                    "the transport for the next recovery attempt"
                )
            self.transport_verified = False
            if not self._announce_enabled:
                logger.info("Worker network recovery cancelled by lifecycle stop")
                return
            self.start(expected_peer_id=previous_peer_id)
            self._verified_transport_failure = None
            recovered_peer_id = self.get_peer_id()
            self._last_network_recovery_peer_id_after = recovered_peer_id
            self._last_network_recovery_identity_preserved = bool(
                previous_peer_id is not None
                and recovered_peer_id == previous_peer_id
            )
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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _components_running(self) -> bool:
        return bool(
            self._running
            and self.dht     is not None
            and self.handler is not None and self.handler.is_loaded()
            and self.rpc     is not None and self.rpc.is_running()
        )

    def is_running(self) -> bool:
        with self._lifecycle_lock:
            announcement = self.get_announcement_status()
            return bool(
                self._components_running()
                and announcement["thread_alive"]
            )

    def get_announcement_status(self) -> dict:
        with self._lifecycle_lock, self._announce_lock:
            success_age = (
                max(0.0, time.time() - self._last_announce_success_at)
                if self._last_announce_success_at is not None
                else None
            )
            return {
                "thread_alive": bool(
                    self._announce_thread is not None
                    and self._announce_thread.is_alive()
                ),
                "last_attempt_at": self._last_announce_attempt_at,
                "last_success_at": self._last_announce_success_at,
                "success_age_seconds": success_age,
                "last_error": self._last_announce_error,
                "consecutive_failures": self._consecutive_announce_failures,
                "consecutive_publication_failures": (
                    self._consecutive_publication_failures
                ),
                "publication_state": (
                    "healthy"
                    if self._publication_outcomes
                    and all(
                        outcome.publication_safe
                        for outcome in self._publication_outcomes.values()
                    )
                    else "degraded"
                    if self._publication_outcomes
                    else "unknown"
                ),
                "publication_outcomes": {
                    key: outcome.to_dict()
                    for key, outcome in self._publication_outcomes.items()
                },
                "transport_recovery_eligible": bool(
                    (transport_failure := self._transport_failure_outcome())
                    is not None
                    and transport_failure.permits_transport_recovery
                ),
                "network_recovery_count": self._network_recovery_count,
                "last_network_recovery_at": self._last_network_recovery_at,
                "last_network_recovery_error": self._last_network_recovery_error,
                "persistent_peer_identity": True,
                "last_network_recovery_trigger": (
                    self._last_network_recovery_trigger
                ),
                "last_network_recovery_failure_count": (
                    self._last_network_recovery_failure_count
                ),
                "last_network_recovery_peer_id_before": (
                    self._last_network_recovery_peer_id_before
                ),
                "last_network_recovery_peer_id_after": (
                    self._last_network_recovery_peer_id_after
                ),
                "last_network_recovery_identity_preserved": (
                    self._last_network_recovery_identity_preserved
                ),
                "fresh": bool(
                    self._last_announce_success_at is not None
                    and success_age is not None
                    and success_age < DHT_EXPIRY_TIME
                ),
            }

    def get_info(self) -> dict:
        with self._lifecycle_lock:
            peer_id = self.get_peer_id()
            rpc = self.rpc
            handler = self.handler
            capability_getter = getattr(rpc, "get_receipt_capability", None)
            receipt_capability = (
                capability_getter(peer_id)
                if callable(capability_getter) and peer_id is not None
                else None
            )
            safety_getter = getattr(rpc, "get_safety_snapshot", None)
            rpc_safety = safety_getter() if callable(safety_getter) else None
            publication_getter = getattr(rpc, "get_publication_status", None)
            rpc_publication = (
                publication_getter()
                if callable(publication_getter)
                else None
            )
            announcement = self.get_announcement_status()
            return {
                "peer_id": peer_id,
                "node_id": self.node_id,
                "rpc_uid": rpc.get_uid() if rpc else None,
                "rpc_uid_schema_version": EXPERT_RPC_UID_SCHEMA_VERSION,
                "rpc_peer_id": peer_id,
                "model_name": self.model_name,
                "placement_model_revision": self.placement_model_revision,
                "layer_start": self.layer_start,
                "layer_end": self.layer_end,
                "device": self.device,
                "running": bool(
                    self._components_running()
                    and announcement["thread_alive"]
                ),
                "maddrs": self.get_visible_maddrs(),
                "layers_loaded": handler.is_loaded() if handler else False,
                "rpc_running": rpc.is_running() if rpc else False,
                "serving_cleanup_pending": self.has_pending_serving_cleanup(),
                "connection_mode": self.connection_mode,
                "direct_reachability": self.direct_reachability,
                "transport_verified": self.transport_verified,
                "loading": getattr(handler, "load_diagnostics", None),
                "rpc_safety": rpc_safety,
                "rpc_publication": rpc_publication,
                "announcement": announcement,
                "placement": dict(self.placement_lease) if self.placement_lease else None,
                "accounting": self._get_accounting_snapshot_owned(peer_id),
                **(receipt_capability or {}),
            }

    def get_accounting_snapshot(self) -> dict:
        with self._lifecycle_lock:
            return self._get_accounting_snapshot_owned(self.get_peer_id())

    def _get_accounting_snapshot_owned(self, peer_id: Optional[str]) -> dict:
        base = {
            "peer_id": peer_id,
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
            with self._dht_command_lock:
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
            with self._dht_command_lock:
                peer_id = self.dht.peer_id
            self._last_peer_id = str(peer_id) if peer_id else None
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
