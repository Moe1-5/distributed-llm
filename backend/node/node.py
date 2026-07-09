"""
node.py
Represents this machine as a fully functional P2P serving node.

Startup sequence:
    1. Start hivemind DHT
    2. Load transformer layers via InferenceHandler
    3. Start RPC server
    4. Announce to DHT
    5. Keep re-announcing every 30s
"""

import time
import threading
from typing import Optional
from uuid import uuid4

import hivemind
import torch
# from hivemind.utils.networking import get_dht_time          # correct import for 1.1.12
from hivemind.utils.logging import get_logger

from node.handler import InferenceHandler
from node.rpc_server import DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, RPCServer, _run_with_timeout
from constants import ANNOUNCE_INTERVAL, DHT_EXPIRY_TIME

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
        node_id:       Optional[str] = None,
        rpc_uid_suffix: Optional[int] = None,
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
        self.node_id       = node_id or uuid4().hex[:12]
        self.rpc_uid_suffix = rpc_uid_suffix

        self.dht:     Optional[hivemind.DHT]     = None
        self.handler: Optional[InferenceHandler] = None
        self.rpc:     Optional[RPCServer]        = None

        self._running         = False
        self._announce_enabled = False
        self._announce_thread: Optional[threading.Thread] = None
        self._last_peer_id: Optional[str] = None
        self._last_maddrs: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info(
            f"Node starting | model={self.model_name} "
            f"layers={self.layer_start}-{self.layer_end} device={self.device}"
        )

        # Step 1: DHT
        if self.dht is None:
            logger.info("Step 1/4: Starting DHT...")
            self.dht = hivemind.DHT(
                host_maddrs=["/ip4/0.0.0.0/tcp/0"],
                initial_peers=self.initial_peers,
                start=True,
                use_ipfs=False,
            )
        else:
            logger.info("Step 1/4: Reusing existing DHT...")
        if self.dht.peer_id is None:
            raise RuntimeError("DHT started but peer_id is None")
        self._last_peer_id = str(self.dht.peer_id)
        logger.info(f"DHT started. Peer ID: {self.dht.peer_id}")

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
        self.rpc.start()
        if not self.rpc.is_running():
            raise RuntimeError("rpc.start() completed but is_running() is False")

        # Step 4: Announce
        logger.info("Step 4/4: Announcing to DHT...")
        self._running = True
        self._announce_enabled = True
        self._announce()
        self._ensure_announce_thread()

        logger.info(f"Node fully started. Addresses: {self.get_visible_maddrs()}")

    def turn_off(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        logger.info("Node turning off serving while keeping loaded layers...")
        self._running = False
        self._announce_enabled = False
        if self._announce_thread is not None:
            self._announce_thread.join(timeout=0.2)
            self._announce_thread = None
        try:
            self._announce(running=False, rpc_running=False)
        except Exception as e:
            logger.warning("Offline announce failed before RPC shutdown: %s", e)
        if self.rpc is not None:
            self.rpc.stop(timeout=timeout)
            self.rpc = None
        if self.dht is not None:
            dht = self.dht
            _run_with_timeout("node-dht-pause-shutdown", dht.shutdown, timeout)
        self.dht = None
        logger.info(
            "Node serving turned off; loaded layers are preserved and serving handles were released."
        )

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        logger.info("Node stopping...")
        self._running = False
        self._announce_enabled = False
        if self._announce_thread is not None:
            self._announce_thread.join(timeout=0.2)
            self._announce_thread = None
        if self.rpc is not None:
            self.rpc.stop(timeout=timeout)
            self.rpc = None
        if self.handler is not None:
            self.handler.unload()
            self.handler = None
        if self.dht is not None:
            dht = self.dht
            _run_with_timeout("node-dht-shutdown", dht.shutdown, timeout)
            self.dht = None
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
            2. {prefix}.members              — list of all peer_ids
        """
        if self.dht is None:
            return

        peer_id = self.get_peer_id()
        if peer_id is None:
            return
        expiry  = time.time() + DHT_EXPIRY_TIME   # fixed import

        # Write full metadata
        self.dht.store(
            key=f"{self.dht_prefix}.node_info.{peer_id}",
            value={
                "peer_id":       peer_id,
                "node_id":       self.node_id,
                "model_name":    self.model_name,
                "layer_start":   self.layer_start,
                "layer_end":     self.layer_end,
                "device":        self.device,
                "running":       self.is_running() if running is None else running,
                "maddrs":        self.get_visible_maddrs(),
                "layers_loaded": self.handler.is_loaded() if self.handler else False,
                "rpc_running":   (
                    self.rpc.is_running() if self.rpc else False
                ) if rpc_running is None else rpc_running,
                "rpc_uid":       self.rpc.get_uid()       if self.rpc     else None,
                "timestamp":     time.time(),
            },
            expiration_time=expiry,
        )

        # Update members index
        members_key = f"{self.dht_prefix}.members"
        try:
            result   = self.dht.get(members_key, latest=True)
            existing = result.value if (result and isinstance(result.value, list)) else []
            if peer_id not in existing:
                existing.append(peer_id)
            self.dht.store(key=members_key, value=existing, expiration_time=expiry)
        except Exception as e:
            logger.warning(f"Failed to update members index: {e}")

        logger.debug(f"Announced to DHT: layers {self.layer_start}-{self.layer_end}")

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
        while self._announce_enabled:
            time.sleep(ANNOUNCE_INTERVAL)
            if self._announce_enabled:
                try:
                    self._announce()
                except Exception as e:
                    logger.warning(f"Re-announce failed: {e}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return (
            self._running
            and self.dht     is not None
            and self.handler is not None and self.handler.is_loaded()
            and self.rpc     is not None and self.rpc.is_running()
        )

    def get_info(self) -> dict:
        return {
            "peer_id":       self.get_peer_id(),
            "node_id":       self.node_id,
            "model_name":    self.model_name,
            "layer_start":   self.layer_start,
            "layer_end":     self.layer_end,
            "device":        self.device,
            "running":       self.is_running(),
            "maddrs":        self.get_visible_maddrs(),
            "layers_loaded": self.handler.is_loaded() if self.handler else False,
            "rpc_running":   self.rpc.is_running()    if self.rpc     else False,
            "accounting":    self.get_accounting_snapshot(),
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

    def get_visible_maddrs(self) -> list[str]:
        if self.dht is None:
            return self._last_maddrs
        try:
            self._last_maddrs = [str(addr) for addr in self.dht.get_visible_maddrs()]
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
