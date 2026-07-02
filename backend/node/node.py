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

import hivemind
import torch
# from hivemind.utils.networking import get_dht_time          # correct import for 1.1.12
from hivemind.utils.logging import get_logger

from node.handler import InferenceHandler
from node.rpc_server import RPCServer
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

        self.dht:     Optional[hivemind.DHT]     = None
        self.handler: Optional[InferenceHandler] = None
        self.rpc:     Optional[RPCServer]        = None

        self._running         = False
        self._announce_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info(
            f"Node starting | model={self.model_name} "
            f"layers={self.layer_start}-{self.layer_end} device={self.device}"
        )

        # Step 1: DHT
        logger.info("Step 1/4: Starting DHT...")
        self.dht = hivemind.DHT(
            host_maddrs=["/ip4/0.0.0.0/tcp/0"],
            initial_peers=self.initial_peers,
            start=True,
            use_ipfs=False,
        )
        if self.dht.peer_id is None:
            raise RuntimeError("DHT started but peer_id is None")
        logger.info(f"DHT started. Peer ID: {self.dht.peer_id}")

        # Step 2: Load layers
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
        if not self.handler.is_loaded():
            raise RuntimeError("handler.load() completed but is_loaded() is False")

        # Step 3: RPC server
        logger.info("Step 3/4: Starting RPC server...")
        self.rpc = RPCServer(
            handler=self.handler,
            dht=self.dht,
            dht_prefix=self.dht_prefix,
        )
        self.rpc.start()
        if not self.rpc.is_running():
            raise RuntimeError("rpc.start() completed but is_running() is False")

        # Step 4: Announce
        logger.info("Step 4/4: Announcing to DHT...")
        self._running = True
        self._announce()

        self._announce_thread = threading.Thread(
            target=self._announce_loop,
            daemon=True,
            name="node-announce",
        )
        self._announce_thread.start()

        logger.info(f"Node fully started. Addresses: {self.get_visible_maddrs()}")

    def stop(self) -> None:
        logger.info("Node stopping...")
        self._running = False
        if self.rpc     is not None: self.rpc.stop();       self.rpc     = None
        if self.handler is not None: self.handler.unload(); self.handler = None
        if self.dht     is not None: self.dht.shutdown();   self.dht     = None
        logger.info("Node stopped.")

    # ------------------------------------------------------------------
    # DHT Announcement
    # ------------------------------------------------------------------

    def _announce(self) -> None:
        """
        Write node metadata to DHT under two keys:
            1. {prefix}.node_info.{peer_id}  — full metadata
            2. {prefix}.members              — list of all peer_ids
        """
        if self.dht is None:
            return

        peer_id = str(self.dht.peer_id)
        expiry  = time.time() + DHT_EXPIRY_TIME   # fixed import

        # Write full metadata
        self.dht.store(
            key=f"{self.dht_prefix}.node_info.{peer_id}",
            value={
                "peer_id":       peer_id,
                "model_name":    self.model_name,
                "layer_start":   self.layer_start,
                "layer_end":     self.layer_end,
                "device":        self.device,
                "layers_loaded": self.handler.is_loaded() if self.handler else False,
                "rpc_running":   self.rpc.is_running()    if self.rpc     else False,
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

    def _announce_loop(self) -> None:
        while self._running:
            time.sleep(ANNOUNCE_INTERVAL)
            if self._running:
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
            "peer_id":       str(self.dht.peer_id) if self.dht else None,
            "model_name":    self.model_name,
            "layer_start":   self.layer_start,
            "layer_end":     self.layer_end,
            "device":        self.device,
            "running":       self.is_running(),
            "maddrs":        self.get_visible_maddrs(),
            "layers_loaded": self.handler.is_loaded() if self.handler else False,
            "rpc_running":   self.rpc.is_running()    if self.rpc     else False,
        }

    def get_visible_maddrs(self) -> list[str]:
        if self.dht is None:
            return []
        return [str(addr) for addr in self.dht.get_visible_maddrs()]

    def get_peer_id(self) -> Optional[str]:
        return str(self.dht.peer_id) if self.dht else None

    def __repr__(self) -> str:
        return (
            f"Node(model={self.model_name}, "
            f"layers={self.layer_start}-{self.layer_end}, "
            f"running={self.is_running()})"
        )
