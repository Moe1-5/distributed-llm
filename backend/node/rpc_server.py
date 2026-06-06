"""
rpc_server.py
Exposes this node's InferenceHandler over the hivemind P2P network.

UID format fix:
    hivemind requires UIDs matching: ^(([^.])+)([.](?:[0]|([1-9]([0-9]*))))+$
    Meaning the UID must end with a DOT followed by a NUMBER.
    e.g. "distribllm.expert.0" is valid
         "distribllm.12D3KooWAbc..." is NOT valid (letters after dot)

    We use: "{dht_prefix}.expert.0"
    And store the actual peer_id in the DHT node_info entry so
    sequential.py can look up the UID from the metadata.
"""

import threading
from typing import Optional

import torch
import torch.nn as nn
import hivemind
from hivemind.moe.server import ModuleBackend
from hivemind.utils.tensor_descr import BatchTensorDescriptor
from hivemind.utils.logging import get_logger

from node.handler import InferenceHandler

logger = get_logger(__name__)


class _HandlerModule(nn.Module):
    """
    Thin nn.Module wrapper so hivemind can call our InferenceHandler.
    Stored as a plain attribute (not nn.Parameter) so hivemind doesn't
    try to serialize the full model weights over the network.
    """

    def __init__(self, handler: InferenceHandler):
        super().__init__()
        assert handler.is_loaded(), "Handler must be loaded before wrapping"
        self._handler = handler

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        return self._handler.forward(hidden_states=hidden_states)


class RPCServer:
    """
    Registers this node's layers with hivemind and serves
    remote forward pass requests.
    """

    def __init__(
        self,
        handler:    InferenceHandler,
        dht:        hivemind.DHT,
        dht_prefix: str,
    ):
        assert handler.is_loaded(),     "RPCServer requires a loaded InferenceHandler"
        assert dht.peer_id is not None, "RPCServer requires a started DHT"
        assert dht_prefix.strip(),      "dht_prefix must not be empty"

        self.handler    = handler
        self.dht        = dht
        self.dht_prefix = dht_prefix

        self._server:  Optional[hivemind.moe.Server] = None
        self._running  = False
        self._uid:     Optional[str] = None
        self._lock     = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                logger.warning("RPCServer already running")
                return

            # UID must match: ^(([^.])+)([.](?:[0]|([1-9]([0-9]*))))+$
            # i.e. segments separated by dots, last segment must be a number
            # We use prefix.expert.0 — simple and always valid
            self._uid   = f"{self.dht_prefix}.0.0"
            hidden_size = self._get_hidden_size()

            logger.info(f"Starting RPC | uid={self._uid} | hidden_size={hidden_size}")

            module = _HandlerModule(self.handler)

            # BatchTensorDescriptor tells hivemind the per-sample tensor shape.
            # We pass (hidden_size,) — hivemind handles batching internally.
            descriptor = BatchTensorDescriptor(2048, hidden_size)

            backend = ModuleBackend(
                name=self._uid,
                module=module,
                args_schema=(descriptor,),
                outputs_schema=(descriptor,),
                max_batch_size=4096,
            )

            self._server = hivemind.moe.Server(
                dht=self.dht,
                module_backends={self._uid: backend},
                num_connection_handlers=4,
                device=torch.device(self.handler.device),
            )

            self._server.run_in_background(await_ready=True)
            self._running = True
            logger.info(f"RPC server running | uid={self._uid}")

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server = None
            self._running = False
            logger.info("RPC server stopped.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def get_uid(self) -> Optional[str]:
        return self._uid

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_hidden_size(self) -> int:
        """Infer hidden_size from first layer's weights."""
        assert self.handler.layers is not None, "Handler layers are None"

        first_layer = self.handler.layers[0]

        # Try common attention projection names
        for attr_path in (
            "self_attn.q_proj",
            "attention.q_proj",
            "self_attention.query_key_value",
            "attn.c_attn",
        ):
            try:
                obj = first_layer
                for part in attr_path.split("."):
                    obj = getattr(obj, part)
                if hasattr(obj, "weight") and obj.weight.dim() >= 2:
                    return obj.weight.shape[1]
            except AttributeError:
                continue

        # Fallback: most common dim across all parameters
        dim_counts: dict[int, int] = {}
        for param in first_layer.parameters():
            if param.dim() >= 2:
                d = param.shape[-1]
                dim_counts[d] = dim_counts.get(d, 0) + 1

        if dim_counts:
            return max(dim_counts, key=lambda d: dim_counts[d])

        raise RuntimeError("Cannot infer hidden_size from model layers")
