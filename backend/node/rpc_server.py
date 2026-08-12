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
import time
import inspect
from queue import Full
from typing import Optional

import torch
import torch.nn as nn
import hivemind
from hivemind.moe.server import ModuleBackend
from hivemind.moe.server.task_pool import Task, TaskPool
from hivemind.proto.runtime_pb2 import CompressionType
from hivemind.utils.tensor_descr import BatchTensorDescriptor
from hivemind.utils.logging import get_logger
from hivemind.utils.mpfuture import MPFuture

from incentives.config import IncentivesConfig, get_incentives_config
from incentives.identity import ApplicationIdentity, load_application_identity
from incentives.protocol import (
    METADATA_TENSOR_SIZE,
    PROTOCOL_VERSION,
    decode_metadata_tensor,
    encode_metadata_tensor,
)
from incentives.receipts import create_worker_receipt, verify_inference_request
from node.handler import InferenceHandler
from node.rpc_safety import (
    RPCOverloadedError,
    RPCSafetyConfig,
    RPCSafetyController,
    RPCSafetyError,
    get_rpc_safety_config,
)

logger = get_logger(__name__)

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
ACTIVATION_COMPRESSION = CompressionType.FLOAT16


class _RejectingTaskPool(TaskPool):
    """Keep Hivemind batching but reject instead of blocking on a full queue."""

    safety_controller: RPCSafetyController

    def submit_task(self, *args: torch.Tensor):
        task = Task(MPFuture(), args)
        if self.get_task_size(task) > self.max_batch_size:
            self.safety_controller.record_transport_rejection("batch")
            task.future.set_exception(
                RPCSafetyError(
                    "batch",
                    f"task exceeds max batch size {self.max_batch_size}",
                )
            )
            return task.future
        try:
            self.tasks.put(task, block=False)
        except Full:
            self.safety_controller.record_transport_rejection("overloaded")
            task.future.set_exception(
                RPCOverloadedError(
                    "rpc_safety:overloaded: worker RPC queue capacity is full"
                )
            )
        else:
            self.undispatched_task_timestamps.put(time.time())
        return task.future


def _install_rejecting_pools(
    backend: ModuleBackend,
    safety: RPCSafetyController,
) -> ModuleBackend:
    for pool in (backend.forward_pool, backend.backward_pool):
        pool.__class__ = _RejectingTaskPool
        pool.safety_controller = safety
    return backend


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

    def __init__(
        self,
        handler: InferenceHandler,
        safety: RPCSafetyController,
        rpc_uid: str = "unknown",
    ):
        super().__init__()
        if not handler.is_loaded():
            raise RuntimeError("Handler must be loaded before wrapping")
        self._handler = handler
        self._safety = safety
        self._rpc_uid = rpc_uid
        self._supports_deadline = "deadline" in inspect.signature(handler.forward).parameters

    def forward(
            self,
            hidden_states: torch.Tensor,
            attention_mask: Optional[torch.Tensor] = None,
            position_ids: Optional[torch.Tensor] = None,
            ) -> torch.Tensor:
        
        self._safety.validate(hidden_states, attention_mask, position_ids)

        def execute(deadline: float) -> torch.Tensor:
            kwargs = dict(
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )
            if self._supports_deadline:
                kwargs["deadline"] = deadline
            return self._handler.forward(**kwargs)

        started_at = time.perf_counter()
        try:
            output = self._safety.execute(hidden_states, execute)
        except Exception:
            logger.exception(
                "Expert forward failed | rpc_uid=%s layers=%s-%s shape=%s bytes=%s",
                self._rpc_uid,
                self._handler.layer_start,
                self._handler.layer_end,
                tuple(hidden_states.shape),
                hidden_states.numel() * hidden_states.element_size(),
            )
            raise
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "Expert forward complete | rpc_uid=%s layers=%s-%s shape=%s bytes=%s "
            "elapsed_ms=%.1f",
            self._rpc_uid,
            self._handler.layer_start,
            self._handler.layer_end,
            tuple(hidden_states.shape),
            hidden_states.numel() * hidden_states.element_size(),
            elapsed_ms,
        )
        return output


class _ReceiptHandlerModule(nn.Module):
    """Receipt-capable wrapper registered under a separate optional expert UID."""

    def __init__(
        self,
        handler: InferenceHandler,
        identity: ApplicationIdentity,
        peer_id: str,
        rpc_uid: str,
        model_revision: str,
        safety: Optional[RPCSafetyController] = None,
    ) -> None:
        super().__init__()
        self._handler = handler
        self._identity = identity
        self._peer_id = peer_id
        self._rpc_uid = rpc_uid
        self._model_revision = model_revision
        self._safety = safety
        parameters = inspect.signature(handler.forward).parameters
        self._supports_deadline = "deadline" in parameters
        self._supports_deferred_accounting = "record_accounting" in parameters

    def forward(
        self,
        hidden_states: torch.Tensor,
        receipt_metadata: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if self._safety is None:
            self._safety = RPCSafetyController(
                get_rpc_safety_config(),
                int(hidden_states.shape[-1]),
            )
        self._safety.validate(
            hidden_states,
            attention_mask,
            position_ids,
            receipt_metadata,
        )

        def execute(deadline: float) -> tuple[torch.Tensor, torch.Tensor]:
            outputs: list[torch.Tensor] = []
            receipts: list[torch.Tensor] = []
            started_at = time.perf_counter()
            for index in range(hidden_states.shape[0]):
                sample_started_at = time.perf_counter()
                sample = hidden_states[index : index + 1]
                request_document = decode_metadata_tensor(
                    receipt_metadata[index : index + 1]
                )
                request = verify_inference_request(
                    request_document,
                    worker_public_key=self._identity.public_key,
                    worker_peer_id=self._peer_id,
                    rpc_uid=self._rpc_uid,
                    layer_start=self._handler.layer_start,
                    layer_end=self._handler.layer_end,
                    hidden_states=sample,
                )
                if request["model_name"] != self._handler.model_name:
                    raise ValueError("Inference request model does not match this worker")
                if request["model_revision"] != self._model_revision:
                    raise ValueError("Inference request revision does not match this worker")
                kwargs = dict(
                    hidden_states=sample,
                    attention_mask=(
                        attention_mask[index : index + 1]
                        if attention_mask is not None
                        else None
                    ),
                    position_ids=(
                        position_ids[index : index + 1]
                        if position_ids is not None
                        else None
                    ),
                )
                if self._supports_deadline:
                    kwargs["deadline"] = deadline
                if self._supports_deferred_accounting:
                    kwargs["record_accounting"] = False
                output = self._handler.forward(**kwargs)
                logger.info(
                    "Receipt expert forward complete | request=%s layers=%s-%s "
                    "shape=%s bytes=%s elapsed_ms=%.1f",
                    request["request_id"],
                    self._handler.layer_start,
                    self._handler.layer_end,
                    tuple(sample.shape),
                    sample.numel() * sample.element_size(),
                    (time.perf_counter() - sample_started_at) * 1000,
                )
                receipt = create_worker_receipt(
                    self._identity,
                    request,
                    output,
                    worker_peer_id=self._peer_id,
                )
                outputs.append(output)
                receipts.append(
                    encode_metadata_tensor(
                        {
                            "worker_receipt": receipt,
                            "worker_presence": self._identity.presence(self._peer_id),
                        }
                    )
                )
            result = torch.cat(outputs, dim=0), torch.cat(receipts, dim=0)
            if self._supports_deferred_accounting:
                self._handler.record_external_result(
                    success=True,
                    token_positions=int(hidden_states.shape[0] * hidden_states.shape[1]),
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )
            return result

        return self._safety.execute(hidden_states, execute)


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
        incentives_config: Optional[IncentivesConfig] = None,
        application_identity: Optional[ApplicationIdentity] = None,
        safety_config: Optional[RPCSafetyConfig] = None,
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
        self.incentives_config = incentives_config or get_incentives_config()
        self.safety_config = safety_config or get_rpc_safety_config()
        self.application_identity = (
            application_identity
            if application_identity is not None
            else (
                load_application_identity()
                if self.incentives_config.enabled
                else None
            )
        )

        self._server:  Optional[hivemind.moe.Server] = None
        self._running  = False
        self._uid:     Optional[str] = None
        self._receipt_uid: Optional[str] = None
        self._lock     = threading.Lock()
        self.safety_controller: Optional[RPCSafetyController] = None

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
        return RPCServer.build_rpc_uid(
            f"{dht_prefix}.999999",
            layer_start,
            layer_end,
            uid_suffix,
        )

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
            self.safety_controller = RPCSafetyController(
                self.safety_config,
                hidden_size,
            )

            logger.info(f"Starting RPC | uid={self._uid} | hidden_size={hidden_size}")

            module = _HandlerModule(self.handler, self.safety_controller, self._uid)
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
            
            hidden_descriptor = BatchTensorDescriptor(
                self.safety_config.max_sequence_length,
                hidden_size,
                compression=ACTIVATION_COMPRESSION,
            )

            # attention_mask is [batch, seq_len] → per-sample shape is (seq_len,)
            # We use (2048,) to match the max sequence length.
            mask_descriptor = BatchTensorDescriptor(
                self.safety_config.max_sequence_length
            )

            backend = _install_rejecting_pools(ModuleBackend(
                name=self._uid,
                module=module,
                args_schema=(hidden_descriptor,),
                kwargs_schema={
                    "attention_mask": mask_descriptor,
                    "position_ids"  : mask_descriptor,
                },
                outputs_schema=(hidden_descriptor,),
                max_batch_size=self.safety_config.max_batch_size,
                pool_size=self.safety_config.max_queued_forwards,
            ), self.safety_controller)
            module_backends = {self._uid: backend}
            if self.incentives_config.enabled:
                if self.application_identity is None:
                    raise RuntimeError("Incentives mode requires an application identity")
                self._receipt_uid = self.build_receipt_rpc_uid(
                    self.dht_prefix,
                    self.handler.layer_start,
                    self.handler.layer_end,
                    self.uid_suffix,
                )
                metadata_descriptor = BatchTensorDescriptor(
                    METADATA_TENSOR_SIZE,
                    dtype=torch.uint8,
                )
                receipt_hidden_descriptor = BatchTensorDescriptor(
                    self.safety_config.max_sequence_length,
                    hidden_size,
                    compression=CompressionType.NONE,
                )
                receipt_backend = _install_rejecting_pools(ModuleBackend(
                    name=self._receipt_uid,
                    module=_ReceiptHandlerModule(
                        self.handler,
                        self.application_identity,
                        str(self.dht.peer_id),
                        self._receipt_uid,
                        self.incentives_config.model_revision,
                        self.safety_controller,
                    ),
                    args_schema=(receipt_hidden_descriptor, metadata_descriptor),
                    kwargs_schema={
                        "attention_mask": mask_descriptor,
                        "position_ids": mask_descriptor,
                    },
                    outputs_schema=(receipt_hidden_descriptor, metadata_descriptor),
                    max_batch_size=self.safety_config.max_batch_size,
                    pool_size=self.safety_config.max_queued_forwards,
                ), self.safety_controller)
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
                num_connection_handlers=max(
                    1,
                    self.safety_config.max_concurrent_forwards
                    + self.safety_config.max_queued_forwards,
                ),
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

    def get_safety_snapshot(self) -> Optional[dict]:
        if self.safety_controller is None:
            return None
        return self.safety_controller.snapshot()

    def get_receipt_capability(self, peer_id: str) -> Optional[dict]:
        if (
            not self._running
            or self._receipt_uid is None
            or self.application_identity is None
        ):
            return None
        return {
            "receipt_protocol_version": PROTOCOL_VERSION,
            "receipt_rpc_uid": self._receipt_uid,
            "application_public_key": self.application_identity.public_key,
            "application_presence": self.application_identity.presence(peer_id),
            "model_revision": self.incentives_config.model_revision,
        }

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
