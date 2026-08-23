"""
handler.py
Loads transformer layers and handles forward pass requests.
Thread-safe — multiple requests queue via a lock.
"""

import threading
import time
from typing import Optional

import torch
import torch.nn as nn
from hivemind.utils.logging import get_logger
from transformers import DynamicCache

from node.block_loader import load_layers
from node.rpc_safety import RPCExecutionTimeout
from node.session_cache import SessionCacheManager

logger = get_logger(__name__)


class InferenceHandler:
    def __init__(
        self,
        model_name:  str,
        layer_start: int,
        layer_end:   int,
        device:      str = "cuda",
        dtype:       torch.dtype = torch.float16,
        hf_token:    Optional[str] = None,
        local_model_path: Optional[str] = None,
    ):
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if layer_end <= layer_start:
            raise ValueError(
                f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
            )
        if device not in ("cuda", "cpu"):
            raise ValueError("device must be 'cuda' or 'cpu'")

        self.model_name  = model_name
        self.layer_start = layer_start
        self.layer_end   = layer_end
        self.device      = device
        self.dtype       = dtype
        self.hf_token    = hf_token
        self.local_model_path = local_model_path

        self.layers: Optional[nn.ModuleList] = None
        self.load_diagnostics: Optional[dict] = None
        self._rotary_embedding: Optional[nn.Module] = None
        self._lock   = threading.Lock()
        self._accounting_lock = threading.Lock()
        self._loaded = False
        self._requests_served = 0
        self._failed_requests = 0
        self._token_positions_served = 0
        self._total_latency_ms = 0.0
        self._last_success_at: Optional[float] = None
        self._last_error_at: Optional[float] = None
        self._session_cache_manager: Optional[SessionCacheManager] = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        logger.info(
            f"Loading layers {self.layer_start}-{self.layer_end} "
            f"of {self.model_name} onto {self.device}..."
        )
        self.layers = load_layers(
            model_name=self.model_name,
            layer_start=self.layer_start,
            layer_end=self.layer_end,
            device=self.device,
            dtype=self.dtype,
            hf_token=self.hf_token,
            local_model_path=self.local_model_path,
        )
        if self.layers is None:
            raise RuntimeError("load_layers returned None")
        if len(self.layers) == 0:
            raise RuntimeError("load_layers returned empty ModuleList")
        expected_layers = self.layer_end - self.layer_start
        if len(self.layers) != expected_layers:
            raise RuntimeError(
                f"Expected {expected_layers} layers, got {len(self.layers)}"
            )
        self.load_diagnostics = getattr(self.layers, "load_diagnostics", None)
        first_layer = self.layers[0]
        model_config = getattr(getattr(first_layer, "self_attn", None), "config", None)
        if "opt" in self.model_name.lower() and model_config is not None:
            self._session_cache_manager = SessionCacheManager(
                layer_count=len(self.layers),
                hidden_size=int(getattr(model_config, "hidden_size")),
                element_size=torch.tensor([], dtype=self.dtype).element_size(),
                model_config=model_config,
            )
        self._loaded = True
        logger.info(f"Loaded {len(self.layers)} layers on {self.device}")

    def unload(self) -> None:
        if self._session_cache_manager is not None:
            self._session_cache_manager.close_all("worker_unload")
            self._session_cache_manager = None
        if self.layers is not None:
            del self.layers
            self.layers = None
            if self.device == "cuda":
                torch.cuda.empty_cache()
        self._loaded = False
        self.load_diagnostics = None
        self._rotary_embedding = None
        logger.info("Layers unloaded.")

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def forward(
        self,
        hidden_states:  torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
        deadline: Optional[float] = None,
        record_accounting: bool = True,
    ) -> torch.Tensor:
        started_at = time.perf_counter()
        token_positions = 0
        if not self._loaded or self.layers is None:
            if record_accounting:
                self._record_accounting(
                    success=False,
                    token_positions=0,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )
            raise RuntimeError("Layers not loaded. Call load() first.")

        if hidden_states.dim() != 3:
            if record_accounting:
                self._record_accounting(
                    success=False,
                    token_positions=0,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )
            raise ValueError(
                f"hidden_states must be [batch, seq_len, hidden_size], got {hidden_states.shape}"
            )
        token_positions = int(hidden_states.shape[0] * hidden_states.shape[1])

        try:
            self._check_deadline(deadline)
            hidden_states = hidden_states.to(self.device, dtype=self.dtype)
            position_ids = self._prepare_position_ids(position_ids, hidden_states)
            attention_mask = self._prepare_decoder_attention_mask(
                attention_mask,
                hidden_states,
            )
            position_embeddings = self._prepare_position_embeddings(
                hidden_states,
                position_ids,
            )

            self._acquire_execution_lock(deadline)
            try:
                with torch.no_grad():
                    for layer in self.layers:
                        self._check_deadline(deadline)
                        layer_kwargs = {
                            "attention_mask": attention_mask,
                            "position_ids": position_ids,
                        }
                        if position_embeddings is not None:
                            layer_kwargs["position_embeddings"] = position_embeddings

                        out = layer(
                            hidden_states,
                            **layer_kwargs,
                        )
                        hidden_states = out[0] if isinstance(out, tuple) else out
                    self._check_deadline(deadline)
            finally:
                self._lock.release()

            if record_accounting:
                self._record_accounting(
                    success=True,
                    token_positions=token_positions,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )
            return hidden_states.cpu()
        except Exception:
            if record_accounting:
                self._record_accounting(
                    success=False,
                    token_positions=token_positions,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                )
            raise

    def session_open(
        self,
        *,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
    ) -> dict:
        manager = self._require_session_cache()
        return manager.open(
            session_id=session_id,
            route_id=route_id,
            request_id=request_id,
            operation_id=operation_id,
        )

    def session_forward(
        self,
        *,
        operation: str,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
        position_start: int,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        position_ids: Optional[torch.Tensor],
        deadline: Optional[float] = None,
    ) -> tuple[torch.Tensor, dict]:
        if operation not in {"prefill", "decode"}:
            raise ValueError("session_forward operation must be prefill or decode")
        if hidden_states.shape[0] != 1:
            raise ValueError("session protocol v1 requires batch size one")
        manager = self._require_session_cache()
        input_bytes = sum(
            value.numel() * value.element_size()
            for value in (hidden_states, attention_mask, position_ids)
            if value is not None
        )

        def execute(cache: DynamicCache) -> torch.Tensor:
            return self._forward_with_cache(
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
                cache=cache,
                position_start=position_start,
                deadline=deadline,
            )

        started_at = time.perf_counter()
        try:
            output, session = manager.execute(
                session_id=session_id,
                route_id=route_id,
                request_id=request_id,
                operation_id=operation_id,
                operation=operation,
                position_start=position_start,
                token_count=int(hidden_states.shape[1]),
                input_bytes=input_bytes,
                target=execute,
            )
        except Exception:
            self._record_accounting(
                success=False,
                token_positions=int(hidden_states.shape[1]),
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise
        self._record_accounting(
            success=True,
            token_positions=int(hidden_states.shape[1]),
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
        return output, session

    def session_close(
        self,
        *,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
        cancelled: bool,
    ) -> dict:
        return self._require_session_cache().close(
            session_id=session_id,
            route_id=route_id,
            request_id=request_id,
            operation_id=operation_id,
            cancelled=cancelled,
        )

    def get_session_cache_snapshot(self) -> dict:
        if self._session_cache_manager is None:
            return {
                "protocol_version": 1,
                "supported": False,
                "active_sessions": 0,
            }
        return {
            "supported": True,
            **self._session_cache_manager.snapshot(),
        }

    def close_all_sessions(self, reason: str) -> int:
        if self._session_cache_manager is None:
            return 0
        return self._session_cache_manager.close_all(reason)

    def _require_session_cache(self) -> SessionCacheManager:
        if not self._loaded or self.layers is None:
            raise RuntimeError("Layers are not loaded")
        if self._session_cache_manager is None:
            raise RuntimeError(
                f"Session protocol v1 is not supported for {self.model_name}"
            )
        return self._session_cache_manager

    def _forward_with_cache(
        self,
        *,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        position_ids: Optional[torch.Tensor],
        cache: DynamicCache,
        position_start: int,
        deadline: Optional[float],
    ) -> torch.Tensor:
        if self.layers is None:
            raise RuntimeError("Layers are not loaded")
        self._check_deadline(deadline)
        hidden_states = hidden_states.to(self.device, dtype=self.dtype)
        position_ids = self._prepare_position_ids(position_ids, hidden_states)
        if bool((position_ids != torch.arange(
            position_start,
            position_start + hidden_states.shape[1],
            device=position_ids.device,
            dtype=position_ids.dtype,
        ).unsqueeze(0)).any()):
            raise ValueError("session position_ids do not match the expected position range")
        attention_mask = self._prepare_decoder_attention_mask(
            attention_mask,
            hidden_states,
            past_length=position_start,
        )
        self._acquire_execution_lock(deadline)
        try:
            with torch.no_grad():
                for layer in self.layers:
                    self._check_deadline(deadline)
                    output = layer(
                        hidden_states,
                        attention_mask=attention_mask,
                        position_ids=position_ids,
                        past_key_values=cache,
                        use_cache=True,
                        cache_position=position_ids[0],
                    )
                    hidden_states = output[0] if isinstance(output, tuple) else output
                self._check_deadline(deadline)
        finally:
            self._lock.release()
        return hidden_states.cpu()

    @staticmethod
    def _check_deadline(deadline: Optional[float]) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise RPCExecutionTimeout(
                "rpc_safety:execution_timeout: request exceeded its layer deadline"
            )

    def _acquire_execution_lock(self, deadline: Optional[float]) -> None:
        if deadline is None:
            self._lock.acquire()
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            raise RPCExecutionTimeout(
                "rpc_safety:execution_timeout: request timed out waiting for model execution"
            )

    def record_external_result(
        self,
        *,
        success: bool,
        token_positions: int,
        latency_ms: float,
    ) -> None:
        """Commit accounting after a multi-sample receipt batch is fully accepted."""
        self._record_accounting(success, token_positions, latency_ms)

    def _record_accounting(
        self,
        success: bool,
        token_positions: int,
        latency_ms: float,
    ) -> None:
        with self._accounting_lock:
            if success:
                self._requests_served += 1
                self._token_positions_served += token_positions
                self._last_success_at = time.time()
            else:
                self._failed_requests += 1
                self._last_error_at = time.time()
            self._total_latency_ms += latency_ms

    def _prepare_position_ids(
        self,
        position_ids: Optional[torch.Tensor],
        hidden_states: torch.Tensor,
    ) -> torch.Tensor:
        batch_size, seq_len, _ = hidden_states.shape
        if position_ids is None:
            return torch.arange(
                seq_len,
                device=hidden_states.device,
                dtype=torch.long,
            ).unsqueeze(0).expand(batch_size, -1)

        position_ids = position_ids.to(hidden_states.device, dtype=torch.long)
        if position_ids.shape != (batch_size, seq_len):
            raise ValueError(
                f"position_ids must be [batch, seq_len] = {(batch_size, seq_len)}, "
                f"got {tuple(position_ids.shape)}"
            )
        return position_ids

    def _prepare_decoder_attention_mask(
        self,
        attention_mask: Optional[torch.Tensor],
        hidden_states: torch.Tensor,
        past_length: int = 0,
    ) -> torch.Tensor:
        batch_size, seq_len, _ = hidden_states.shape
        total_length = past_length + seq_len
        device = hidden_states.device
        dtype = hidden_states.dtype

        if attention_mask is None:
            token_mask = torch.ones(
                (batch_size, total_length),
                device=device,
                dtype=torch.bool,
            )
        else:
            attention_mask = attention_mask.to(device)
            if attention_mask.dim() == 4:
                return attention_mask.to(dtype=dtype)
            if attention_mask.dim() != 2:
                raise ValueError(
                    "attention_mask must be [batch, seq_len] or "
                    f"[batch, 1, tgt_len, src_len], got {tuple(attention_mask.shape)}"
                )
            if past_length > 0 and attention_mask.shape == (batch_size, seq_len):
                attention_mask = torch.cat(
                    [
                        torch.ones(
                            (batch_size, past_length),
                            device=device,
                            dtype=attention_mask.dtype,
                        ),
                        attention_mask,
                    ],
                    dim=1,
                )
            if attention_mask.shape != (batch_size, total_length):
                raise ValueError(
                    "attention_mask must cover cached and current positions as "
                    f"{(batch_size, total_length)}, "
                    f"got {tuple(attention_mask.shape)}"
                )
            token_mask = attention_mask.to(dtype=torch.bool)

        source_positions = torch.arange(total_length, device=device)
        target_positions = torch.arange(
            past_length,
            total_length,
            device=device,
        )
        allowed = (source_positions.unsqueeze(0) <= target_positions.unsqueeze(1))
        allowed = allowed.unsqueeze(0).unsqueeze(0)
        allowed = allowed & token_mask[:, None, None, :]

        decoder_mask = torch.zeros(
            (batch_size, 1, seq_len, total_length),
            device=device,
            dtype=dtype,
        )
        return decoder_mask.masked_fill(~allowed, torch.finfo(dtype).min)

    def _prepare_position_embeddings(
        self,
        hidden_states: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> Optional[tuple[torch.Tensor, torch.Tensor]]:
        model_name = self.model_name.lower()
        if "llama" not in model_name and "mistral" not in model_name:
            return None

        if self._rotary_embedding is None:
            if self.layers is None or len(self.layers) == 0:
                raise RuntimeError("Cannot prepare rotary embeddings without loaded layers")
            first_layer = self.layers[0]
            config = getattr(getattr(first_layer, "self_attn", None), "config", None)
            if config is None:
                raise ValueError(
                    f"{self.model_name} layers require rotary embeddings but no layer config was found"
                )
            if "mistral" in model_name:
                from transformers.models.mistral.modeling_mistral import MistralRotaryEmbedding

                self._rotary_embedding = MistralRotaryEmbedding(config=config)
            else:
                from transformers.models.llama.modeling_llama import LlamaRotaryEmbedding

                self._rotary_embedding = LlamaRotaryEmbedding(config=config)
            self._rotary_embedding.to(self.device)
            self._rotary_embedding.eval()

        return self._rotary_embedding(hidden_states, position_ids)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._loaded

    def get_info(self) -> dict:
        return {
            "model_name":  self.model_name,
            "layer_start": self.layer_start,
            "layer_end":   self.layer_end,
            "device":      self.device,
            "loaded":      self._loaded,
            "num_layers":  len(self.layers) if self.layers else 0,
            "loading":     self.load_diagnostics,
            "session_cache": self.get_session_cache_snapshot(),
        }

    def get_accounting_snapshot(self) -> dict:
        with self._accounting_lock:
            total_requests = self._requests_served + self._failed_requests
            avg_latency_ms = (
                self._total_latency_ms / total_requests
                if total_requests > 0
                else 0.0
            )
            return {
                "model_name": self.model_name,
                "layer_start": self.layer_start,
                "layer_end": self.layer_end,
                "layers_served": self.layer_end - self.layer_start,
                "device": self.device,
                "requests_served": self._requests_served,
                "failed_requests": self._failed_requests,
                "token_positions_served": self._token_positions_served,
                "total_latency_ms": self._total_latency_ms,
                "avg_latency_ms": avg_latency_ms,
                "last_success_at": self._last_success_at,
                "last_error_at": self._last_error_at,
            }
