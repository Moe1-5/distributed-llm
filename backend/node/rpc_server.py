"""
rpc_server.py
Exposes this node's InferenceHandler over the hivemind P2P network.

UID format:
    hivemind requires UIDs matching: ^(([^.])+)([.](?:[0]|([1-9]([0-9]*))))+$
    Meaning the UID must end with a DOT followed by a NUMBER.
    e.g. "distribllm.expert.0" is valid
         "distribllm.12D3KooWAbc..." is NOT valid (letters after dot)

    We put the stable Base58 peer ID in Hivemind's non-numeric prefix and use
    numeric role, layer, and replica coordinates. Independent devices serving
    the same range therefore do not compete for one expert key.
"""

import asyncio
import os
import threading
import time
import inspect
from queue import Full
from typing import Optional

import torch
import torch.nn as nn
import hivemind
from hivemind.p2p import P2P
from hivemind.moe.server import ModuleBackend
from hivemind.moe.expert_uid import is_valid_uid
from hivemind.moe.server.task_pool import Task, TaskPool
from hivemind.proto.runtime_pb2 import CompressionType
from hivemind.utils.tensor_descr import BatchTensorDescriptor
from hivemind.utils.logging import get_logger
from hivemind.utils.mpfuture import MPFuture

from incentives.config import (
    IncentivesConfig,
    get_incentives_config,
    is_immutable_model_revision,
)
from incentives.identity import ApplicationIdentity, load_application_identity
from incentives.protocol import (
    METADATA_TENSOR_SIZE,
    PROTOCOL_VERSION,
    decode_metadata_tensor,
    encode_metadata_tensor,
)
from incentives.receipts import create_worker_receipt, verify_inference_request
from constants import ANNOUNCE_INTERVAL, DHT_EXPIRY_TIME
from node.handler import InferenceHandler
from node.rpc_safety import (
    RPCOverloadedError,
    RPCSafetyConfig,
    RPCSafetyController,
    RPCSafetyError,
    get_rpc_safety_config,
)
from node.session_protocol import (
    SESSION_METADATA_TENSOR_SIZE,
    SESSION_PROTOCOL_VERSION,
    decode_session_metadata,
    encode_session_metadata,
    validate_session_operation,
)

logger = get_logger(__name__)

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
ACTIVATION_COMPRESSION = CompressionType.FLOAT16


class _RejectingTaskPool(TaskPool):
    """Keep Hivemind batching but reject instead of blocking on a full queue."""

    safety_controller: RPCSafetyController

    def submit_task(self, *args: torch.Tensor):
        task = Task(_new_rpc_task_future(self.name), args)
        logger.debug(
            "RPC task admitted | pool=%s pid=%s thread=%s shape=%s bytes=%s",
            self.name,
            os.getpid(),
            threading.current_thread().name,
            tuple(args[0].shape) if args else None,
            sum(arg.numel() * arg.element_size() for arg in args),
        )
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


def _new_rpc_task_future(pool_name: str) -> MPFuture:
    """Create an MPFuture that is always awaitable by the RPC handler loop."""
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        logger.error(
            "RPC task admission rejected | pool=%s pid=%s thread=%s "
            "error=event_loop_unavailable",
            pool_name,
            os.getpid(),
            threading.current_thread().name,
        )
        raise RuntimeError(
            "rpc_transport:event_loop_unavailable: expert task was admitted "
            "outside the Hivemind RPC event loop"
        ) from exc

    future = MPFuture()
    bound_loop = getattr(future, "_loop", None)
    aio_event = getattr(future, "_aio_event", None)
    if bound_loop is not running_loop or aio_event is None:
        # Hivemind 1.1.12 uses asyncio.get_event_loop() internally. On Python
        # 3.12 that can leave MPFuture unbound even while an RPC loop is active.
        future._loop = running_loop
        future._aio_event = asyncio.Event()
        logger.warning(
            "Rebound Hivemind MPFuture to active RPC loop | pool=%s pid=%s "
            "thread=%s previous_loop=%s",
            pool_name,
            os.getpid(),
            threading.current_thread().name,
            "missing" if bound_loop is None else "different",
        )
    return future


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


class _BorrowedDHT:
    """Delegate a worker DHT while keeping shutdown ownership in ``Node``.

    Hivemind 1.1.12's ``Server.shutdown`` always shuts down the supplied DHT.
    The worker lifecycle also owns that DHT, so the server receives this
    non-owning view and may stop its own processes without double-closing the
    worker transport.
    """

    def __init__(
        self,
        dht: hivemind.DHT,
        command_lock,
        *,
        p2p_daemon_listen_maddr=None,
    ) -> None:
        self._dht = dht
        self._command_lock = command_lock
        self._owner_pid = os.getpid()
        if p2p_daemon_listen_maddr is None:
            # Hivemind 1.1.12's DHT.replicate_p2p() first sends a private
            # run_coroutine command through DHT._outer_pipe, then creates a
            # P2P replica from the returned daemon address. Resolve that one
            # address while still in the owning process. ConnectionHandler is
            # a ForkProcess that Hivemind may force-terminate during shutdown;
            # it must never own the lock protecting the shared command pipe.
            with self._command_lock:
                p2p_daemon_listen_maddr = dht.run_coroutine(
                    hivemind.DHT._get_p2p_daemon_listen_maddr
                )
        if p2p_daemon_listen_maddr is None:
            raise RuntimeError("Worker DHT returned no P2P daemon listen address")
        self._p2p_daemon_listen_maddr = p2p_daemon_listen_maddr

    def _require_owner_process(self, name: str) -> None:
        if os.getpid() != self._owner_pid:
            raise RuntimeError(
                "Borrowed worker DHT access is restricted to its owner process; "
                f"child processes may only call replicate_p2p(), not {name}"
            )

    def __getattr__(self, name: str):
        self._require_owner_process(name)
        with self._command_lock:
            attribute = getattr(self._dht, name)
        if not callable(attribute):
            return attribute

        def _serialized_call(*args, **kwargs):
            self._require_owner_process(name)
            with self._command_lock:
                return attribute(*args, **kwargs)

        return _serialized_call

    async def replicate_p2p(self):
        # Hivemind connection handlers await this coroutine in forked
        # processes. Replicate directly from the parent-captured daemon address
        # so a force-killed handler never holds a lock or writes to the shared
        # DHT command pipe.
        return await P2P.replicate(self._p2p_daemon_listen_maddr)

    def shutdown(self) -> None:
        logger.debug("Hivemind server released its borrowed worker DHT view")


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


class _SessionHandlerModule(nn.Module):
    """Stateful OPT prefill/decode expert under a separate versioned UID."""

    def __init__(
        self,
        handler: InferenceHandler,
        safety: RPCSafetyController,
        *,
        peer_id: str,
        rpc_uid: str,
    ) -> None:
        super().__init__()
        self._handler = handler
        self._safety = safety
        self._peer_id = peer_id
        self._rpc_uid = rpc_uid

    def forward(
        self,
        hidden_states: torch.Tensor,
        session_metadata: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        self._safety.validate(
            hidden_states,
            attention_mask,
            position_ids,
            session_metadata,
        )
        if hidden_states.shape[0] != 1:
            raise ValueError("Session protocol v1 requires batch size one")
        operation = validate_session_operation(
            decode_session_metadata(session_metadata),
            expected_peer_id=self._peer_id,
            expected_rpc_uid=self._rpc_uid,
            expected_layer_start=self._handler.layer_start,
            expected_layer_end=self._handler.layer_end,
        )
        logger.info(
            "Session RPC admitted | request=%s session=%s operation=%s "
            "position=%s tokens=%s layers=%s-%s shape=%s bytes=%s",
            operation["request_id"],
            operation["session_id"],
            operation["operation"],
            operation["position_start"],
            operation["token_count"],
            self._handler.layer_start,
            self._handler.layer_end,
            tuple(hidden_states.shape),
            hidden_states.numel() * hidden_states.element_size(),
        )

        def execute(deadline: float) -> tuple[torch.Tensor, torch.Tensor]:
            action = str(operation["operation"])
            if action == "open":
                session = self._handler.session_open(
                    session_id=operation["session_id"],
                    route_id=operation["route_id"],
                    request_id=operation["request_id"],
                    operation_id=operation["operation_id"],
                )
                output = hidden_states.detach().to(device="cpu")
            elif action in {"prefill", "decode"}:
                if int(operation["token_count"]) != int(hidden_states.shape[1]):
                    raise ValueError("Session token_count does not match hidden states")
                output, session = self._handler.session_forward(
                    operation=action,
                    session_id=operation["session_id"],
                    route_id=operation["route_id"],
                    request_id=operation["request_id"],
                    operation_id=operation["operation_id"],
                    position_start=int(operation["position_start"]),
                    hidden_states=hidden_states,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                    deadline=deadline,
                )
            else:
                session = self._handler.session_close(
                    session_id=operation["session_id"],
                    route_id=operation["route_id"],
                    request_id=operation["request_id"],
                    operation_id=operation["operation_id"],
                    cancelled=action == "cancel",
                )
                output = hidden_states.detach().to(device="cpu")
            response = {
                "protocol_version": SESSION_PROTOCOL_VERSION,
                "operation": action,
                "operation_id": operation["operation_id"],
                "session": session,
            }
            logger.info(
                "Session RPC complete | request=%s session=%s operation=%s "
                "next_position=%s cache_bytes=%s retained_replay=%s",
                operation["request_id"],
                operation["session_id"],
                action,
                session.get("expected_position"),
                session.get("estimated_bytes"),
                bool(session.get("operation_replayed")),
            )
            return output, encode_session_metadata(response)

        try:
            return self._safety.execute(hidden_states, execute)
        except Exception as exc:
            logger.error(
                "Session RPC failed | request=%s session=%s operation=%s "
                "position=%s error=%s: %s",
                operation["request_id"],
                operation["session_id"],
                operation["operation"],
                operation["position_start"],
                type(exc).__name__,
                exc,
            )
            raise


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
        dht_command_lock=None,
    ):
        self._dht_command_lock = (
            dht_command_lock
            if dht_command_lock is not None
            else threading.RLock()
        )
        if not handler.is_loaded():
            raise RuntimeError("RPCServer requires a loaded InferenceHandler")
        with self._dht_command_lock:
            dht_peer_id = dht.peer_id
        if dht_peer_id is None:
            raise RuntimeError("RPCServer requires a started DHT")
        if not dht_prefix.strip():
            raise ValueError("dht_prefix must not be empty")

        self.handler    = handler
        self.dht        = dht
        self.dht_prefix = dht_prefix
        self.uid_suffix = uid_suffix
        self.incentives_config = incentives_config or get_incentives_config()
        self._receipt_model_revision: Optional[str] = None
        if self.incentives_config.enabled:
            diagnostics = getattr(handler, "load_diagnostics", None)
            loaded_revision = (
                str(diagnostics.get("model_revision", "")).strip()
                if isinstance(diagnostics, dict)
                else ""
            )
            if not is_immutable_model_revision(loaded_revision):
                raise RuntimeError(
                    "Incentives require the loaded model to expose an exact "
                    "lowercase 40-character checkpoint commit hash"
                )
            expected_revision = self.incentives_config.model_revision
            if expected_revision and expected_revision != loaded_revision:
                raise RuntimeError(
                    "Loaded model revision does not match "
                    "DISTRIBLLM_MODEL_REVISION: "
                    f"expected {expected_revision}, got {loaded_revision}"
                )
            self._receipt_model_revision = loaded_revision
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
        self._session_uid: Optional[str] = None
        self._lock     = threading.Lock()
        self._shutdown_thread: Optional[threading.Thread] = None
        self._shutdown_server: Optional[hivemind.moe.Server] = None
        self._shutdown_error: Optional[BaseException] = None
        self._publication_lock = threading.RLock()
        self._last_publication_attempt_at: Optional[float] = None
        self._last_publication_success_at: Optional[float] = None
        self._last_publication_expiration_time: Optional[float] = None
        self._last_publication_error: Optional[str] = None
        self._consecutive_publication_failures = 0
        self.require_remote_publication = True
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
        *,
        provider_peer_id: Optional[str] = None,
    ) -> str:
        normalized_prefix = dht_prefix.strip()
        if not normalized_prefix:
            raise ValueError("dht_prefix must not be empty")
        if layer_start < 0:
            raise ValueError(f"layer_start must be >= 0, got {layer_start}")
        if layer_end <= layer_start:
            raise ValueError(
                f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
            )
        if uid_suffix is not None and uid_suffix < 0:
            raise ValueError(f"uid_suffix must be >= 0, got {uid_suffix}")
        if provider_peer_id is None:
            base_uid = f"{normalized_prefix}.{layer_start}.{layer_end}"
            rpc_uid = base_uid if uid_suffix is None else f"{base_uid}.{uid_suffix}"
            if not is_valid_uid(rpc_uid):
                raise ValueError(
                    f"dht_prefix produces an invalid Hivemind expert UID: {rpc_uid!r}"
                )
            return rpc_uid

        normalized_peer_id = str(provider_peer_id).strip()
        if "." in normalized_prefix:
            raise ValueError("dht_prefix must not contain dots for peer-addressed RPC")
        if not normalized_peer_id:
            raise ValueError("provider_peer_id must not be empty")
        if "." in normalized_peer_id:
            raise ValueError("provider_peer_id must not contain dots")
        replica = 0 if uid_suffix is None else uid_suffix
        return (
            f"{normalized_prefix}-{normalized_peer_id}.0."
            f"{layer_start}.{layer_end}.{replica}"
        )

    @staticmethod
    def build_receipt_rpc_uid(
        dht_prefix: str,
        layer_start: int,
        layer_end: int,
        uid_suffix: Optional[int] = None,
        *,
        provider_peer_id: Optional[str] = None,
    ) -> str:
        if provider_peer_id is not None:
            normalized_prefix = dht_prefix.strip()
            normalized_peer_id = str(provider_peer_id).strip()
            if not normalized_prefix:
                raise ValueError("dht_prefix must not be empty")
            if "." in normalized_prefix:
                raise ValueError("dht_prefix must not contain dots for peer-addressed RPC")
            if not normalized_peer_id:
                raise ValueError("provider_peer_id must not be empty")
            if "." in normalized_peer_id:
                raise ValueError("provider_peer_id must not contain dots")
            if uid_suffix is not None and uid_suffix < 0:
                raise ValueError(f"uid_suffix must be >= 0, got {uid_suffix}")
            replica = 0 if uid_suffix is None else uid_suffix
            return (
                f"{normalized_prefix}-{normalized_peer_id}.1."
                f"{layer_start}.{layer_end}.{replica}"
            )
        return RPCServer.build_rpc_uid(
            f"{dht_prefix}.999999",
            layer_start,
            layer_end,
            uid_suffix,
        )

    @staticmethod
    def build_session_rpc_uid(
        dht_prefix: str,
        layer_start: int,
        layer_end: int,
        uid_suffix: Optional[int] = None,
        *,
        provider_peer_id: Optional[str] = None,
    ) -> str:
        if provider_peer_id is None:
            return RPCServer.build_rpc_uid(
                f"{dht_prefix}.888888",
                layer_start,
                layer_end,
                uid_suffix,
            )
        normalized_prefix = dht_prefix.strip()
        normalized_peer_id = str(provider_peer_id).strip()
        if not normalized_prefix or "." in normalized_prefix:
            raise ValueError("dht_prefix must be non-empty and dot-free")
        if not normalized_peer_id or "." in normalized_peer_id:
            raise ValueError("provider_peer_id must be non-empty and dot-free")
        replica = 0 if uid_suffix is None else uid_suffix
        if replica < 0:
            raise ValueError("uid_suffix must be non-negative")
        return (
            f"{normalized_prefix}-{normalized_peer_id}.2."
            f"{layer_start}.{layer_end}.{replica}"
        )

    def start(self) -> None:
        with self._lock:
            if self._running:
                logger.warning("RPCServer already running")
                return

            with self._dht_command_lock:
                provider_peer_id = str(self.dht.peer_id)

            # UID must match: ^(([^.])+)([.](?:[0]|([1-9]([0-9]*))))+$
            # i.e. segments separated by dots, last segment must be a number
            # Version two embeds the stable peer in the text prefix and keeps
            # all following coordinates numeric for Hivemind 1.1.12.
            self._uid = self.build_rpc_uid(
                self.dht_prefix,
                self.handler.layer_start,
                self.handler.layer_end,
                self.uid_suffix,
                provider_peer_id=provider_peer_id,
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
            session_snapshot_getter = getattr(
                self.handler,
                "get_session_cache_snapshot",
                None,
            )
            session_snapshot = (
                session_snapshot_getter()
                if callable(session_snapshot_getter)
                else {"supported": False}
            )
            if session_snapshot.get("supported"):
                self._session_uid = self.build_session_rpc_uid(
                    self.dht_prefix,
                    self.handler.layer_start,
                    self.handler.layer_end,
                    self.uid_suffix,
                    provider_peer_id=provider_peer_id,
                )
                session_metadata_descriptor = BatchTensorDescriptor(
                    SESSION_METADATA_TENSOR_SIZE,
                    dtype=torch.uint8,
                )
                session_backend = _install_rejecting_pools(ModuleBackend(
                    name=self._session_uid,
                    module=_SessionHandlerModule(
                        self.handler,
                        self.safety_controller,
                        peer_id=provider_peer_id,
                        rpc_uid=self._session_uid,
                    ),
                    args_schema=(hidden_descriptor, session_metadata_descriptor),
                    kwargs_schema={
                        "attention_mask": mask_descriptor,
                        "position_ids": mask_descriptor,
                    },
                    outputs_schema=(hidden_descriptor, session_metadata_descriptor),
                    max_batch_size=1,
                    pool_size=self.safety_config.max_queued_forwards,
                ), self.safety_controller)
                module_backends[self._session_uid] = session_backend
            if self.incentives_config.enabled:
                if self.application_identity is None:
                    raise RuntimeError("Incentives mode requires an application identity")
                if self._receipt_model_revision is None:
                    raise RuntimeError(
                        "Incentives mode requires an immutable loaded model revision"
                    )
                self._receipt_uid = self.build_receipt_rpc_uid(
                    self.dht_prefix,
                    self.handler.layer_start,
                    self.handler.layer_end,
                    self.uid_suffix,
                    provider_peer_id=provider_peer_id,
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
                        provider_peer_id,
                        self._receipt_uid,
                        self._receipt_model_revision,
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
                dht=_BorrowedDHT(self.dht, self._dht_command_lock),
                module_backends=module_backends,
                num_connection_handlers=max(
                    1,
                    self.safety_config.max_concurrent_forwards
                    + self.safety_config.max_queued_forwards,
                ),
                device=torch.device(self.handler.device),
                update_period=ANNOUNCE_INTERVAL,
                expiration=DHT_EXPIRY_TIME,
            )

            self._server.run_in_background(await_ready=True)
            self._running = True
            logger.info(
                "RPC server running | uid=%s publication_period=%ss "
                "publication_expiry=%ss",
                self._uid,
                ANNOUNCE_INTERVAL,
                DHT_EXPIRY_TIME,
            )

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> bool:
        with self._lock:
            server = self._server
            if server is None:
                self._running = False
                self._receipt_uid = None
                self._session_uid = None
                close_sessions = getattr(
                    getattr(self, "handler", None),
                    "close_all_sessions",
                    None,
                )
                if callable(close_sessions):
                    close_sessions("rpc_not_running")
                return True

            shutdown_thread = getattr(self, "_shutdown_thread", None)
            shutdown_server = getattr(self, "_shutdown_server", None)
            if shutdown_thread is not None and shutdown_server is not server:
                logger.error(
                    "RPC shutdown ownership mismatch; retaining both server handles"
                )
                return False

            if shutdown_thread is None:
                self._shutdown_error = None
                self._shutdown_server = server

                def _shutdown() -> None:
                    try:
                        server.shutdown()
                    except BaseException as exc:
                        self._shutdown_error = exc

                shutdown_thread = threading.Thread(
                    target=_shutdown,
                    daemon=True,
                    name="rpc-server-shutdown",
                )
                self._shutdown_thread = shutdown_thread
                shutdown_thread.start()

            shutdown_thread.join(timeout=max(0.0, timeout))
            if shutdown_thread.is_alive():
                logger.warning(
                    "RPC server shutdown did not finish within %.3g seconds; "
                    "retaining the live server and shutdown attempt for retry",
                    timeout,
                )
                return False

            shutdown_error = getattr(self, "_shutdown_error", None)
            if shutdown_error is not None:
                logger.warning(
                    "RPC server shutdown failed; retaining ownership for retry: %s",
                    shutdown_error,
                    exc_info=(
                        type(shutdown_error),
                        shutdown_error,
                        shutdown_error.__traceback__,
                    ),
                )
                return False

            self._shutdown_thread = None
            self._shutdown_server = None
            self._shutdown_error = None

            if self._server is server:
                self._server = None
            self._running = False
            self._receipt_uid = None
            self._session_uid = None
            close_sessions = getattr(
                getattr(self, "handler", None),
                "close_all_sessions",
                None,
            )
            if callable(close_sessions):
                close_sessions("rpc_shutdown")
            logger.info("RPC server stopped.")
            return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        server = self._server
        return bool(
            self._running
            and server is not None
            and self._server_thread_alive(server)
            and self._server_runtime_ready(server)
        )

    def refresh_publication(self, expiration_time: float) -> dict:
        """Observe Hivemind's sole expert publisher without writing its keys.

        The pinned Hivemind server owns expert UID and prefix declarations via
        ``DHTHandlerThread``. DistribLLM previously wrote the same UIDs here,
        racing that publisher and interpreting a harmless newer record as a
        failed transport. Peer-addressed dispatch no longer resolves the
        selected worker through those DHT keys, so this method is diagnostic.
        """
        with self._publication_lock:
            self._last_publication_attempt_at = time.time()
            uids = self._publication_uids()
            try:
                if not self.is_running():
                    raise RuntimeError("RPC runtime is not running")
                if not uids:
                    raise RuntimeError("RPC server has no expert UIDs to publish")
                server = self._server
                publisher = getattr(server, "dht_handler_thread", None)
                if not self._thread_alive(publisher):
                    raise RuntimeError("Hivemind expert publisher is not running")
                self._last_publication_success_at = time.time()
                self._last_publication_expiration_time = expiration_time
                self._last_publication_error = None
                self._consecutive_publication_failures = 0
                logger.debug(
                    "Hivemind expert publisher observed | uids=%s expiry=%s",
                    uids,
                    expiration_time,
                )
            except Exception as exc:
                self._last_publication_error = f"{type(exc).__name__}: {exc}"
                self._consecutive_publication_failures += 1
                logger.warning(
                    "RPC expert publication failed | uids=%s failures=%s error=%s",
                    uids,
                    self._consecutive_publication_failures,
                    self._last_publication_error,
                )
                raise
            return self.get_publication_status()

    def get_publication_status(self) -> dict:
        with self._publication_lock:
            server = self._server
            success_age = (
                max(0.0, time.time() - self._last_publication_success_at)
                if self._last_publication_success_at is not None
                else None
            )
            server_alive = bool(
                server is not None and self._server_thread_alive(server)
            )
            runtime_ready = bool(
                server is not None and self._server_runtime_ready(server)
            )
            publisher = getattr(server, "dht_handler_thread", None)
            publisher_alive = self._thread_alive(publisher)
            return {
                "uids": self._publication_uids(),
                "server_alive": server_alive,
                "runtime_ready": runtime_ready,
                "hivemind_publisher_alive": publisher_alive,
                "last_attempt_at": self._last_publication_attempt_at,
                "last_success_at": self._last_publication_success_at,
                "last_expiration_time": self._last_publication_expiration_time,
                "success_age_seconds": success_age,
                "last_error": self._last_publication_error,
                "consecutive_failures": self._consecutive_publication_failures,
                "fresh": bool(
                    self._running
                    and server_alive
                    and runtime_ready
                    and publisher_alive
                    and success_age is not None
                    and success_age < DHT_EXPIRY_TIME
                ),
                "remote_store_required": False,
                "publication_owner": "hivemind_server",
                "manual_store_enabled": False,
                "independent_remote_verified": False,
            }

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
            "model_revision": self._receipt_model_revision,
        }

    def get_session_capability(self) -> Optional[dict]:
        if not self._running or self._session_uid is None:
            return None
        snapshot_getter = getattr(self.handler, "get_session_cache_snapshot", None)
        return {
            "session_protocol_version": SESSION_PROTOCOL_VERSION,
            "session_rpc_uid": self._session_uid,
            "session_hidden_size": self._get_hidden_size(),
            "session_cache": (
                snapshot_getter()
                if callable(snapshot_getter)
                else {"supported": False, "active_sessions": 0}
            ),
        }

    def _publication_uids(self) -> list[str]:
        return [
            uid
            for uid in (
                getattr(self, "_uid", None),
                getattr(self, "_receipt_uid", None),
                getattr(self, "_session_uid", None),
            )
            if uid is not None
        ]

    @staticmethod
    def _thread_alive(thread) -> bool:
        if thread is None:
            return False
        try:
            return bool(thread.is_alive())
        except Exception:
            return False

    @classmethod
    def _server_thread_alive(cls, server) -> bool:
        return cls._thread_alive(server)

    @staticmethod
    def _server_runtime_ready(server) -> bool:
        ready = getattr(server, "ready", None)
        if ready is None:
            return False
        try:
            return bool(ready.is_set())
        except Exception:
            return False

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
