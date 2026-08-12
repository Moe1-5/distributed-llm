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
from incentives.protocol import (
    MAX_RECEIPT_BYTES,
    PROTOCOL_VERSION,
    create_worker_receipt,
    decode_envelope,
    encode_envelope,
    validate_work_request,
)
from incentives.runtime import get_incentive_mode, get_runtime_identity

logger = get_logger(__name__)

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _run_with_timeout(name: str, target, timeout: float) -> bool:
    done = threading.Event()

    def _run() -> None:
        try:
            target()
        except Exception as exc:
            logger.warning("%s raised during shutdown: %s", name, exc, exc_info=True)
        finally:
            done.set()

    thread = threading.Thread(target=_run, daemon=True, name=name)
    thread.start()
    finished = done.wait(timeout)
    if not finished:
        logger.warning("%s did not finish within %.1fs; continuing shutdown", name, timeout)
    return finished


class _HandlerModule(nn.Module):
    """
    Thin nn.Module wrapper so hivemind can call our InferenceHandler.
    Stored as a plain attribute (not nn.Parameter) so hivemind doesn't
    try to serialize the full model weights over the network.
    """

    def __init__(self, handler: InferenceHandler):
        super().__init__()
        if not handler.is_loaded():
            raise RuntimeError("Handler must be loaded before wrapping")
        self._handler = handler

    def forward(
            self,
            hidden_states: torch.Tensor,
            attention_mask: Optional[torch.Tensor] = None,
            position_ids: Optional[torch.Tensor] = None,
            ) -> torch.Tensor:
        
        return self._handler.forward(hidden_states=hidden_states, attention_mask=attention_mask, position_ids=position_ids)


class _ReceiptHandlerModule(nn.Module):
    """Receipt-capable forward path; registered separately for compatibility."""

    def __init__(self, handler: InferenceHandler, peer_id: str):
        super().__init__()
        self._handler = handler
        self._peer_id = peer_id
        self._identity = get_runtime_identity()

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        receipt_request: Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if receipt_request is None or receipt_request.dim() != 2:
            raise ValueError("receipt_request must be a batch of encoded metadata rows")
        if receipt_request.shape[0] != hidden_states.shape[0]:
            raise ValueError("receipt_request batch must match hidden_states batch")

        outputs: list[torch.Tensor] = []
        receipts: list[torch.Tensor] = []
        for index in range(hidden_states.shape[0]):
            hidden = hidden_states[index : index + 1]
            request = validate_work_request(decode_envelope(receipt_request[index]), hidden)
            if request["model_name"] != self._handler.model_name:
                raise ValueError("Work request model does not match serving node")
            if (
                int(request["layer_start"]) != self._handler.layer_start
                or int(request["layer_end"]) != self._handler.layer_end
            ):
                raise ValueError("Work request layer range does not match serving node")
            if request["worker_peer_id"] != self._peer_id:
                raise ValueError("Work request targets a different peer")

            output = self._handler.forward(
                hidden_states=hidden,
                attention_mask=attention_mask[index : index + 1] if attention_mask is not None else None,
                position_ids=position_ids[index : index + 1] if position_ids is not None else None,
            )
            receipt = create_worker_receipt(
                identity=self._identity,
                request=request,
                peer_id=self._peer_id,
                output=output,
                position_count=int(hidden.shape[0] * hidden.shape[1]),
            )
            outputs.append(output)
            receipts.append(encode_envelope(receipt)[0])
        return torch.cat(outputs, dim=0), torch.stack(receipts)


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
        uid_suffix: Optional[int] = None,
    ):
        if not handler.is_loaded():
            raise RuntimeError("RPCServer requires a loaded InferenceHandler")
        if dht.peer_id is None:
            raise RuntimeError("RPCServer requires a started DHT")
        if not dht_prefix.strip():
            raise ValueError("dht_prefix must not be empty")

        self.handler    = handler
        self.dht        = dht
        self.dht_prefix = dht_prefix
        self.uid_suffix = uid_suffix

        self._server:  Optional[hivemind.moe.Server] = None
        self._running  = False
        self._uid:     Optional[str] = None
        self._receipt_uid: Optional[str] = None
        self._lock     = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def build_rpc_uid(
        dht_prefix: str,
        layer_start: int,
        layer_end: int,
        uid_suffix: Optional[int] = None,
    ) -> str:
        if not dht_prefix.strip():
            raise ValueError("dht_prefix must not be empty")
        if layer_start < 0:
            raise ValueError(f"layer_start must be >= 0, got {layer_start}")
        if layer_end <= layer_start:
            raise ValueError(
                f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
            )
        base_uid = f"{dht_prefix}.{layer_start}.{layer_end}"
        if uid_suffix is None:
            return base_uid
        if uid_suffix < 0:
            raise ValueError(f"uid_suffix must be >= 0, got {uid_suffix}")
        return f"{base_uid}.{uid_suffix}"

    @staticmethod
    def build_receipt_rpc_uid(
        dht_prefix: str,
        layer_start: int,
        layer_end: int,
        uid_suffix: Optional[int] = None,
    ) -> str:
        base_uid = f"{dht_prefix}.receipt.{layer_start}.{layer_end}"
        return base_uid if uid_suffix is None else f"{base_uid}.{uid_suffix}"

    def start(self) -> None:
        with self._lock:
            if self._running:
                logger.warning("RPCServer already running")
                return

            # UID must match: ^(([^.])+)([.](?:[0]|([1-9]([0-9]*))))+$
            # i.e. segments separated by dots, last segment must be a number
            # We use prefix.expert.0 — simple and always valid
            self._uid = self.build_rpc_uid(
                self.dht_prefix,
                self.handler.layer_start,
                self.handler.layer_end,
                self.uid_suffix,
            )
            hidden_size = self._get_hidden_size()

            logger.info(f"Starting RPC | uid={self._uid} | hidden_size={hidden_size}")

            module = _HandlerModule(self.handler)
            # ── Tensor schemas ────────────────────────────────────────────────
            # args_schema   → positional args to forward() after self
            # kwargs_schema → keyword args; hivemind stores the keys as
            #                 keyword_names and validates them on every call.
            #
            # args_schema:   (hidden_states,)
            # kwargs_schema: {attention_mask: descriptor, position_ids: descriptor}
            #
            # Both masks use the same shape as hidden_states. BatchTensorDescriptor
            # takes (seq_len, hidden_size) as the per-sample shape; hivemind adds
            # the batch dim automatically.
            #
            # Note: attention_mask is typically [batch, seq_len] (2D), not 3D.
            # If your handler reshapes it internally, you can use a 1D descriptor
            # here — hivemind only uses this for serialization sizing, not strict
            # shape enforcement.
            
            hidden_descriptor = BatchTensorDescriptor(2048, hidden_size)

            # attention_mask is [batch, seq_len] → per-sample shape is (seq_len,)
            # We use (2048,) to match the max sequence length.
            mask_descriptor   = BatchTensorDescriptor(2048)

            backend = ModuleBackend(
                name=self._uid,
                module=module,
                args_schema=(hidden_descriptor,),
                kwargs_schema={
                    "attention_mask": mask_descriptor,
                    "position_ids"  : mask_descriptor,
                },
                outputs_schema=(hidden_descriptor,),
                max_batch_size=4096,
            )

            module_backends = {self._uid: backend}
            if get_incentive_mode() != "off":
                self._receipt_uid = self.build_receipt_rpc_uid(
                    self.dht_prefix,
                    self.handler.layer_start,
                    self.handler.layer_end,
                    self.uid_suffix,
                )
                receipt_descriptor = BatchTensorDescriptor(MAX_RECEIPT_BYTES, dtype=torch.uint8)
                receipt_backend = ModuleBackend(
                    name=self._receipt_uid,
                    module=_ReceiptHandlerModule(self.handler, str(self.dht.peer_id)),
                    args_schema=(hidden_descriptor,),
                    kwargs_schema={
                        "attention_mask": mask_descriptor,
                        "position_ids": mask_descriptor,
                        "receipt_request": receipt_descriptor,
                    },
                    outputs_schema=(hidden_descriptor, receipt_descriptor),
                    max_batch_size=64,
                )
                module_backends[self._receipt_uid] = receipt_backend

            logger.info(
                f"[RPCServer] ModuleBackend registered | uid={self._uid}\n"
                f"  args_schema    : hidden_states {hidden_descriptor}\n"
                f"  kwargs_schema  : attention_mask, position_ids\n"
                # f"  keyword_names  : {backend.get_info('keyword_names', '(not yet set)')}"
            )

            self._server = hivemind.moe.Server(
                dht=self.dht,
                module_backends=module_backends,
                num_connection_handlers=4,
                device=torch.device(self.handler.device),
            )

            self._server.run_in_background(await_ready=True)
            self._running = True
            logger.info(f"RPC server running | uid={self._uid}")

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
        with self._lock:
            server = self._server
            if server is not None:
                _run_with_timeout("rpc-server-shutdown", server.shutdown, timeout)
                self._server = None
            self._running = False
            self._receipt_uid = None
            logger.info("RPC server stopped.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def get_uid(self) -> Optional[str]:
        return self._uid

    def get_receipt_uid(self) -> Optional[str]:
        return self._receipt_uid

    def get_receipt_protocol(self) -> Optional[int]:
        return PROTOCOL_VERSION if self._receipt_uid else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_hidden_size(self) -> int:
        """Infer hidden_size from first layer's weights."""
        if self.handler.layers is None:
            raise RuntimeError("Handler layers are None")

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
