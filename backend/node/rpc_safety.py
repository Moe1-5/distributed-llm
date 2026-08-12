"""Typed validation, bounded admission, and counters for public expert RPC."""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from dataclasses import asdict, dataclass
from typing import Callable, TypeVar

import torch

T = TypeVar("T")


class RPCSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"rpc_safety:{code}: {message}")
        self.code = code


class RPCOverloadedError(RuntimeError):
    pass


class RPCExecutionTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class RPCSafetyConfig:
    max_sequence_length: int = 2048
    max_batch_size: int = 4
    max_tensor_bytes: int = 256 * 1024 * 1024
    max_metadata_bytes: int = 16_380
    max_concurrent_forwards: int = 1
    max_queued_forwards: int = 2
    queue_wait_seconds: float = 0.25
    execution_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_sequence_length <= 131_072:
            raise ValueError("RPC max sequence length must be between 1 and 131072")
        if not 1 <= self.max_batch_size <= 64:
            raise ValueError("RPC max batch size must be between 1 and 64")
        if not 1024 <= self.max_tensor_bytes <= 4 * 1024**3:
            raise ValueError("RPC max tensor bytes must be between 1024 and 4 GiB")
        if not 4 <= self.max_metadata_bytes <= 1024 * 1024:
            raise ValueError("RPC max metadata bytes must be between 4 and 1 MiB")
        if not 1 <= self.max_concurrent_forwards <= 32:
            raise ValueError("RPC max concurrent forwards must be between 1 and 32")
        if not 1 <= self.max_queued_forwards <= 128:
            raise ValueError("RPC max queued forwards must be between 1 and 128")
        if not 0 <= self.queue_wait_seconds <= 30:
            raise ValueError("RPC queue wait must be between 0 and 30 seconds")
        if not 1 <= self.execution_timeout_seconds <= 3600:
            raise ValueError("RPC execution timeout must be between 1 and 3600 seconds")

    def public_dict(self) -> dict:
        return asdict(self)


class RPCSafetyController:
    _REJECTION_CODES = (
        "shape",
        "batch",
        "sequence",
        "hidden_size",
        "dtype",
        "tensor_bytes",
        "non_finite",
        "mask",
        "position_ids",
        "metadata",
        "overloaded",
        "queue_timeout",
    )

    def __init__(self, config: RPCSafetyConfig, hidden_size: int) -> None:
        if hidden_size <= 0:
            raise ValueError("RPC hidden size must be positive")
        self.config = config
        self.hidden_size = hidden_size
        self._execution_slots = mp.BoundedSemaphore(config.max_concurrent_forwards)
        self._lock = mp.RLock()
        self._accepted = mp.Value("Q", 0)
        self._completed = mp.Value("Q", 0)
        self._failed = mp.Value("Q", 0)
        self._timed_out = mp.Value("Q", 0)
        self._active = mp.Value("i", 0)
        self._peak_active = mp.Value("i", 0)
        self._queued = mp.Value("i", 0)
        self._peak_queued = mp.Value("i", 0)
        self._rejected = {code: mp.Value("Q", 0) for code in self._REJECTION_CODES}

    def validate(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None,
        position_ids: torch.Tensor | None,
        receipt_metadata: torch.Tensor | None = None,
    ) -> None:
        try:
            self._validate_hidden_states(hidden_states)
            self._validate_attention_mask(attention_mask, hidden_states)
            self._validate_position_ids(position_ids, hidden_states)
            self._validate_metadata(receipt_metadata, hidden_states)
            tensor_bytes = sum(
                self._tensor_bytes(value)
                for value in (hidden_states, attention_mask, position_ids)
                if value is not None
            )
            if tensor_bytes > self.config.max_tensor_bytes:
                self._raise("tensor_bytes", "combined inference tensors exceed the byte limit")
        except RPCSafetyError as exc:
            self._increment_rejection(exc.code)
            raise

    def execute(self, hidden_states: torch.Tensor, target: Callable[[float], T]) -> T:
        acquired = self._execution_slots.acquire(block=False)
        if not acquired:
            with self._lock:
                if self._queued.value >= self.config.max_queued_forwards:
                    self._increment_rejection_locked("overloaded")
                    raise RPCOverloadedError(
                        "rpc_safety:overloaded: worker execution and queue capacity are full"
                    )
                self._queued.value += 1
                self._peak_queued.value = max(self._peak_queued.value, self._queued.value)
            acquired = self._execution_slots.acquire(
                block=True,
                timeout=self.config.queue_wait_seconds,
            )
            with self._lock:
                self._queued.value -= 1
            if not acquired:
                self._increment_rejection("queue_timeout")
                raise RPCOverloadedError(
                    "rpc_safety:queue_timeout: worker did not admit the request in time"
                )

        batch_size = int(hidden_states.shape[0])
        with self._lock:
            self._accepted.value += batch_size
            self._active.value += 1
            self._peak_active.value = max(self._peak_active.value, self._active.value)
        deadline = time.monotonic() + self.config.execution_timeout_seconds
        try:
            result = target(deadline)
            if time.monotonic() > deadline:
                raise RPCExecutionTimeout(
                    "rpc_safety:execution_timeout: request exceeded the cooperative deadline"
                )
            with self._lock:
                self._completed.value += batch_size
            return result
        except RPCExecutionTimeout:
            with self._lock:
                self._timed_out.value += batch_size
            raise
        except Exception:
            with self._lock:
                self._failed.value += batch_size
            raise
        finally:
            with self._lock:
                self._active.value -= 1
            self._execution_slots.release()

    def snapshot(self) -> dict:
        with self._lock:
            rejected = {code: value.value for code, value in self._rejected.items()}
            return {
                "policy": self.config.public_dict(),
                "accepted_requests": self._accepted.value,
                "completed_requests": self._completed.value,
                "failed_requests": self._failed.value,
                "rejected_requests": sum(rejected.values()),
                "rejected_by_reason": rejected,
                "timed_out_requests": self._timed_out.value,
                "active_forwards": self._active.value,
                "peak_active_forwards": self._peak_active.value,
                "queued_forwards": self._queued.value,
                "peak_queued_forwards": self._peak_queued.value,
            }

    def record_transport_rejection(self, code: str) -> None:
        if code not in {"batch", "overloaded"}:
            raise ValueError(f"Unsupported transport rejection code: {code}")
        self._increment_rejection(code)

    def _validate_hidden_states(self, value: torch.Tensor) -> None:
        if not isinstance(value, torch.Tensor) or value.dim() != 3:
            self._raise("shape", "hidden_states must have rank 3 [batch, sequence, hidden]")
        batch, sequence, hidden = value.shape
        if not 1 <= batch <= self.config.max_batch_size:
            self._raise("batch", f"batch size {batch} exceeds {self.config.max_batch_size}")
        if not 1 <= sequence <= self.config.max_sequence_length:
            self._raise(
                "sequence",
                f"sequence length {sequence} exceeds {self.config.max_sequence_length}",
            )
        if hidden != self.hidden_size:
            self._raise("hidden_size", f"hidden size {hidden} must equal {self.hidden_size}")
        if value.dtype not in {torch.float16, torch.bfloat16, torch.float32}:
            self._raise("dtype", f"hidden_states dtype {value.dtype} is not supported")
        if self._tensor_bytes(value) > self.config.max_tensor_bytes:
            self._raise("tensor_bytes", "hidden_states exceeds the tensor byte limit")
        if not bool(torch.isfinite(value).all()):
            self._raise("non_finite", "hidden_states contains NaN or infinity")

    def _validate_attention_mask(
        self,
        value: torch.Tensor | None,
        hidden_states: torch.Tensor,
    ) -> None:
        if value is None:
            return
        batch, sequence, _ = hidden_states.shape
        valid_shape = value.shape == (batch, sequence) or value.shape == (
            batch,
            1,
            sequence,
            sequence,
        )
        if value.dim() not in {2, 4} or not valid_shape:
            self._raise(
                "mask",
                "attention_mask must be [batch, sequence] or [batch, 1, sequence, sequence]",
            )
        if value.dtype not in {
            torch.bool,
            torch.uint8,
            torch.int8,
            torch.int16,
            torch.int32,
            torch.int64,
            torch.float16,
            torch.bfloat16,
            torch.float32,
        }:
            self._raise("mask", f"attention_mask dtype {value.dtype} is not supported")
        if self._tensor_bytes(value) > self.config.max_tensor_bytes:
            self._raise("tensor_bytes", "attention_mask exceeds the tensor byte limit")
        if value.is_floating_point() and not bool(torch.isfinite(value).all()):
            self._raise("mask", "attention_mask contains NaN or infinity")
        if value.dim() == 2 and not bool(((value == 0) | (value == 1)).all()):
            self._raise("mask", "two-dimensional attention_mask values must be zero or one")

    def _validate_position_ids(
        self,
        value: torch.Tensor | None,
        hidden_states: torch.Tensor,
    ) -> None:
        if value is None:
            return
        batch, sequence, _ = hidden_states.shape
        if value.dim() != 2 or value.shape != (batch, sequence):
            self._raise("position_ids", "position_ids must be [batch, sequence]")
        if value.dtype not in {torch.int32, torch.int64}:
            self._raise("position_ids", "position_ids must use int32 or int64")
        if self._tensor_bytes(value) > self.config.max_tensor_bytes:
            self._raise("tensor_bytes", "position_ids exceeds the tensor byte limit")
        if bool((value < 0).any()) or bool((value >= self.config.max_sequence_length).any()):
            self._raise("position_ids", "position_ids must stay inside the context limit")

    def _validate_metadata(
        self,
        value: torch.Tensor | None,
        hidden_states: torch.Tensor,
    ) -> None:
        if value is None:
            return
        batch = hidden_states.shape[0]
        if value.dtype != torch.uint8 or value.dim() != 2 or value.shape[0] != batch:
            self._raise("metadata", "receipt metadata must be uint8 [batch, bytes]")
        if value.shape[1] < 4:
            self._raise("metadata", "receipt metadata is missing its length prefix")
        headers = value[:, :4].detach().to(device="cpu", dtype=torch.uint8)
        for header in headers:
            length = int.from_bytes(bytes(header.tolist()), "big")
            if length <= 0 or length > self.config.max_metadata_bytes:
                self._raise("metadata", "receipt metadata payload exceeds the configured byte limit")

    @staticmethod
    def _tensor_bytes(value: torch.Tensor) -> int:
        return value.numel() * value.element_size()

    @staticmethod
    def _raise(code: str, message: str) -> None:
        raise RPCSafetyError(code, message)

    def _increment_rejection(self, code: str) -> None:
        with self._lock:
            self._increment_rejection_locked(code)

    def _increment_rejection_locked(self, code: str) -> None:
        counter = self._rejected.get(code)
        if counter is not None:
            counter.value += 1


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number; got {raw!r}") from exc


def get_rpc_safety_config() -> RPCSafetyConfig:
    return RPCSafetyConfig(
        max_sequence_length=_env_int("DISTRIBLLM_RPC_MAX_SEQUENCE_LENGTH", 2048),
        max_batch_size=_env_int("DISTRIBLLM_RPC_MAX_BATCH_SIZE", 4),
        max_tensor_bytes=_env_int("DISTRIBLLM_RPC_MAX_TENSOR_BYTES", 256 * 1024 * 1024),
        max_metadata_bytes=_env_int("DISTRIBLLM_RPC_MAX_METADATA_BYTES", 16_380),
        max_concurrent_forwards=_env_int("DISTRIBLLM_RPC_MAX_CONCURRENT_FORWARDS", 1),
        max_queued_forwards=_env_int("DISTRIBLLM_RPC_MAX_QUEUED_FORWARDS", 2),
        queue_wait_seconds=_env_float("DISTRIBLLM_RPC_QUEUE_WAIT_SECONDS", 0.25),
        execution_timeout_seconds=_env_float("DISTRIBLLM_RPC_EXECUTION_TIMEOUT", 120.0),
    )
