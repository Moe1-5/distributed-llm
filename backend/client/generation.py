"""
generation.py
Autoregressive text generation using the distributed P2P network.

Local components (this machine):
    - Tokenizer
    - embed_tokens
    - final norm
    - lm_head
    - sampling logic

Remote components (P2P network via RemoteSequential):
    - All transformer decoder layers
"""

import asyncio
import copy
import inspect
import threading
import time
from collections.abc import Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncGenerator, Optional
from uuid import uuid4

import torch
import torch.nn as nn
from hivemind.utils.logging import get_logger
from transformers import AutoTokenizer, AutoModelForCausalLM
from constants import SUPPORTED_MODELS, DEFAULT_GEN_CONFIG
from client.sequential import RemoteSequential
from client.failover import RouteCancellationError
from models.architecture_adapter import get_architecture_adapter

logger = get_logger(__name__)


@dataclass
class _LocalComponentBundle:
    tokenizer: object
    model_shell: nn.Module
    embed_tokens: nn.Embedding
    position_embeddings: Optional[nn.Embedding]
    norm: nn.Module
    lm_head: nn.Linear
    architecture_adapter: object


_COMPONENT_CACHE_LOCK = threading.RLock()
_COMPONENT_CACHE: dict[tuple[str, str, str], _LocalComponentBundle] = {}
_COMPONENT_CACHE_LIMIT = 1


class GeneratorOperationBusyError(RuntimeError):
    """Raised when another request already owns the singleton generator."""


class _ContextAwareTextDecoder:
    """Emit stable deltas from cumulative tokenizer decoding."""

    def __init__(self, tokenizer: object):
        self.tokenizer = tokenizer
        self.token_ids: list[int] = []
        self.emitted_text = ""

    @staticmethod
    def _is_cjk_character(character: str) -> bool:
        codepoint = ord(character)
        return (
            0x4E00 <= codepoint <= 0x9FFF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        )

    def _decode(self) -> str:
        return self.tokenizer.decode(
            self.token_ids,
            skip_special_tokens=True,
        )

    def _delta_through(self, end: int) -> str:
        decoded = self._decode()
        stable_text = decoded[:end]
        if not stable_text.startswith(self.emitted_text):
            raise RuntimeError(
                "Tokenizer changed text that was already streamed; "
                "cannot produce lossless text deltas."
            )
        delta = stable_text[len(self.emitted_text) :]
        self.emitted_text = stable_text
        return delta

    def push(self, token_id: int) -> str:
        self.token_ids.append(int(token_id))
        decoded = self._decode()
        if not decoded:
            return ""
        if decoded.endswith("\n") or self._is_cjk_character(decoded[-1]):
            return self._delta_through(len(decoded))

        last_whitespace = max(
            (index for index, character in enumerate(decoded) if character.isspace()),
            default=-1,
        )
        return self._delta_through(last_whitespace + 1)

    def finish(self) -> str:
        if not self.token_ids:
            return ""
        return self._delta_through(len(self._decode()))


class DistributedGenerator:
    def __init__(
        self,
        model_name: str,
        sequential: RemoteSequential,
        device:     str           = "cpu",
        dtype:      torch.dtype   = torch.float32,
        hf_token:   Optional[str] = None,
        local_model_path: Optional[str] = None,
    ):
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if sequential is None:
            raise ValueError("sequential must not be None")

        self.model_name = model_name
        self.sequential = sequential
        self.device     = device
        self.dtype      = dtype
        self.hf_token   = hf_token
        self.local_model_path = local_model_path

        self.tokenizer:    Optional[object]        = None
        self.embed_tokens: Optional[nn.Embedding]  = None
        self.position_embeddings: Optional[nn.Embedding] = None
        self.norm:         Optional[nn.Module]     = None
        self.lm_head:      Optional[nn.Linear]     = None
        self.architecture_adapter = None
        self._loaded_model: Optional[nn.Module] = None
        self._reference_model: Optional[nn.Module] = None
        self._reference_lock = threading.RLock()
        self._model_shell_pruned = False
        self._component_cache_key: Optional[tuple[str, str, str]] = None
        self._loaded = False
        self._stop_requested = False
        self._stop_event = threading.Event()
        self._operation_condition = threading.Condition(threading.RLock())
        self._active_operations: dict[str, str] = {}
        self._closing = False
        self._load_duration_ms: Optional[float] = None
        self._load_kind = "not_loaded"
        self._cold_load_duration_ms: Optional[float] = None
        self._warm_load_duration_ms: Optional[float] = None
        self._startup_duration_ms: Optional[float] = None
        self._last_generation_metrics: Optional[dict] = None


    def _get_gen_config(self) -> dict:
        """Pull gen config from SUPPORTED_MODELS, fall back to default."""
        model_entry = SUPPORTED_MODELS.get(self.model_name, {})
        cfg = model_entry.get("gen", DEFAULT_GEN_CONFIG)
        logger.info(
            f"[gen-config] {self.model_name}: "
            f"temp={cfg['temperature']} top_p={cfg['top_p']} "
            f"top_k={cfg['top_k']} rep_penalty={cfg['repetition_penalty']}"
        )
        return cfg

    def _encode_prompt(
        self,
        prompt: str,
        device: Optional[torch.device | str] = None,
    ) -> torch.Tensor:
        if self.tokenizer is None:
            raise RuntimeError("Tokenizer is not loaded")

        tuning = SUPPORTED_MODELS.get(self.model_name, {}).get("tuning", "base")
        if tuning in {"chat", "instruct"}:
            apply_chat_template = getattr(self.tokenizer, "apply_chat_template", None)
            if not callable(apply_chat_template):
                raise RuntimeError(
                    f"Tokenizer for {self.model_name} has no chat template for {tuning} prompts."
                )
            try:
                input_ids = apply_chat_template(
                    [{"role": "user", "content": prompt}],
                    tokenize=True,
                    add_generation_prompt=True,
                    return_tensors="pt",
                    return_dict=False,
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Tokenizer for {self.model_name} has no usable chat template."
                ) from exc
        else:
            input_ids = self.tokenizer.encode(prompt, return_tensors="pt")

        if isinstance(input_ids, Mapping):
            input_ids = input_ids.get("input_ids")
            if input_ids is None:
                raise RuntimeError("Chat template output has no input_ids")
        if not isinstance(input_ids, torch.Tensor):
            input_ids = torch.as_tensor(input_ids, dtype=torch.long)
        if input_ids.dim() == 1:
            input_ids = input_ids.unsqueeze(0)
        if input_ids.dim() != 2 or input_ids.shape[0] != 1:
            raise RuntimeError(
                f"Expected encoded prompt shape [1, seq_len], got {tuple(input_ids.shape)}"
            )
        return input_ids.to(device if device is not None else self.device)

    def _validate_generation_inputs(
        self,
        input_ids: torch.Tensor,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> None:
        if input_ids.dim() != 2:
            raise ValueError(f"Expected [batch, seq_len], got {input_ids.shape}")
        if hidden_states.dim() != 3:
            raise ValueError(
                f"Expected hidden_states [batch, seq_len, hidden], got {hidden_states.shape}"
            )
        if attention_mask.shape != input_ids.shape:
            raise ValueError(
                f"Expected attention_mask {input_ids.shape}, got {attention_mask.shape}"
            )
        if position_ids.shape != (1, input_ids.shape[1]):
            raise ValueError(
                f"Expected position_ids [1, seq_len], got {position_ids.shape}"
            )

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self) -> None:
        started_at = time.perf_counter()
        logger.info(
            f"Loading local components for {self.model_name} | "
            f"hf_token={'set' if self.hf_token else 'not set'} | "
            f"local_model_path={'set' if self.local_model_path else 'not set'}"
        )

        source = self.local_model_path or self.model_name
        token_kwargs = (
            {"token": self.hf_token}
            if self.hf_token and not self.local_model_path
            else ({"token": False} if not self.local_model_path else {})
        )
        local_kwargs = {"local_files_only": True} if self.local_model_path else {}

        cache_key = (self.model_name, str(source), str(self.dtype))
        self._component_cache_key = cache_key
        cached = self._take_cached_components(cache_key)
        if cached is not None:
            self._install_component_bundle(cached)
            self._open_operation_admission()
            self._load_kind = "warm_component_cache"
            self._load_duration_ms = (time.perf_counter() - started_at) * 1000
            self._warm_load_duration_ms = self._load_duration_ms
            logger.info("Local components restored from CPU cache in %.1fms.", self._load_duration_ms)
            return

        self.tokenizer = AutoTokenizer.from_pretrained(source, **token_kwargs, **local_kwargs)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if self.tokenizer.eos_token_id is None:
            raise RuntimeError("Tokenizer has no EOS token")

        model = AutoModelForCausalLM.from_pretrained(
            source,
            torch_dtype=self.dtype,
            low_cpu_mem_usage=True,
            device_map="cpu",
            **token_kwargs,
            **local_kwargs,
        )
        model.eval()

        self.embed_tokens = self._extract_embed_tokens(model)
        self.position_embeddings = self._extract_position_embeddings(model)
        self.norm         = self._extract_norm(model)
        self.lm_head      = self._extract_lm_head(model)
        self.architecture_adapter = get_architecture_adapter(self.model_name)
        logger.info(
            "[adapter] selected %s for %s",
            type(self.architecture_adapter).__name__,
            self.model_name,
        )
        self._loaded_model = model

        components = [self.embed_tokens, self.norm, self.lm_head]
        if self.position_embeddings is not None:
            components.append(self.position_embeddings)
        for component in components:
            component.to(self.device)
            component.eval()
            component.requires_grad_(False)

        self._validate_loaded_components()
        self._prune_transformer_blocks(self._loaded_model)
        self._model_shell_pruned = True
        torch.cuda.empty_cache()

        self._open_operation_admission()
        self._load_kind = "cold_model_load"
        self._load_duration_ms = (time.perf_counter() - started_at) * 1000
        self._cold_load_duration_ms = self._load_duration_ms
        logger.info(
            "Local components loaded in %.1fms.",
            self._load_duration_ms,
        )

    def set_startup_duration_ms(self, duration_ms: float) -> None:
        self._startup_duration_ms = max(0.0, float(duration_ms))

    def get_performance_snapshot(self) -> dict:
        return {
            "startup_duration_ms": self._startup_duration_ms,
            "load_duration_ms": self._load_duration_ms,
            "load_kind": self._load_kind,
            "cold_load_duration_ms": self._cold_load_duration_ms,
            "warm_load_duration_ms": self._warm_load_duration_ms,
            "component_cache_entries": self.component_cache_size(),
            "last_generation": copy.deepcopy(self._last_generation_metrics),
        }

    @staticmethod
    def component_cache_size() -> int:
        with _COMPONENT_CACHE_LOCK:
            return len(_COMPONENT_CACHE)

    @staticmethod
    def clear_component_cache() -> None:
        with _COMPONENT_CACHE_LOCK:
            _COMPONENT_CACHE.clear()

    @staticmethod
    def _prune_transformer_blocks(model: nn.Module) -> None:
        decoder = getattr(getattr(model, "model", None), "decoder", None)
        if decoder is not None and hasattr(decoder, "layers"):
            decoder.layers = nn.ModuleList()
            return
        inner_model = getattr(model, "model", None)
        if inner_model is not None and hasattr(inner_model, "layers"):
            inner_model.layers = nn.ModuleList()
            return
        raise ValueError(
            f"Cannot prune transformer blocks from {type(model).__name__}"
        )

    def _component_bundle(self) -> _LocalComponentBundle:
        if any(
            component is None
            for component in (
                self.tokenizer,
                self._loaded_model,
                self.embed_tokens,
                self.norm,
                self.lm_head,
                self.architecture_adapter,
            )
        ):
            raise RuntimeError("Cannot cache incomplete local generator components")
        return _LocalComponentBundle(
            tokenizer=self.tokenizer,
            model_shell=self._loaded_model,
            embed_tokens=self.embed_tokens,
            position_embeddings=self.position_embeddings,
            norm=self.norm,
            lm_head=self.lm_head,
            architecture_adapter=self.architecture_adapter,
        )

    def _store_cached_components(self, key: tuple[str, str, str]) -> None:
        bundle = self._component_bundle()
        for component in (
            bundle.model_shell,
            bundle.embed_tokens,
            bundle.position_embeddings,
            bundle.norm,
            bundle.lm_head,
        ):
            if component is not None:
                component.to("cpu")
        with _COMPONENT_CACHE_LOCK:
            _COMPONENT_CACHE.pop(key, None)
            _COMPONENT_CACHE[key] = bundle
            while len(_COMPONENT_CACHE) > _COMPONENT_CACHE_LIMIT:
                oldest = next(iter(_COMPONENT_CACHE))
                _COMPONENT_CACHE.pop(oldest, None)

    @staticmethod
    def _take_cached_components(
        key: tuple[str, str, str],
    ) -> Optional[_LocalComponentBundle]:
        with _COMPONENT_CACHE_LOCK:
            return _COMPONENT_CACHE.pop(key, None)

    def _install_component_bundle(self, bundle: _LocalComponentBundle) -> None:
        self.tokenizer = bundle.tokenizer
        self._loaded_model = bundle.model_shell
        self.embed_tokens = bundle.embed_tokens
        self.position_embeddings = bundle.position_embeddings
        self.norm = bundle.norm
        self.lm_head = bundle.lm_head
        self.architecture_adapter = bundle.architecture_adapter
        self._model_shell_pruned = True
        components = [self._loaded_model, self.embed_tokens, self.norm, self.lm_head]
        if self.position_embeddings is not None:
            components.append(self.position_embeddings)
        for component in components:
            component.to(self.device)
            component.eval()
            component.requires_grad_(False)
        self._validate_loaded_components()

    def _validate_loaded_components(self) -> None:
        missing = []
        for name in (
            "tokenizer",
            "embed_tokens",
            "norm",
            "lm_head",
            "architecture_adapter",
            "_loaded_model",
        ):
            if getattr(self, name) is None:
                missing.append(name)
        if missing:
            raise RuntimeError(
                "Generator is not ready; missing local component(s): "
                + ", ".join(missing)
            )

        expected_hidden_size = SUPPORTED_MODELS.get(self.model_name, {}).get("hidden_size")
        actual_hidden_size = getattr(self.embed_tokens, "embedding_dim", None)
        if expected_hidden_size is not None and actual_hidden_size is not None:
            if int(actual_hidden_size) != int(expected_hidden_size):
                raise RuntimeError(
                    f"Generator hidden size mismatch for {self.model_name}: "
                    f"registry={expected_hidden_size}, embeddings={actual_hidden_size}"
                )

    def _open_operation_admission(self) -> None:
        """Publish a loaded runtime and reset cancellation for its first request."""
        with self._operation_condition:
            if self._active_operations:
                raise RuntimeError(
                    "Cannot load generator components while operations are active"
                )
            self._loaded = True
            self._closing = False
            self._stop_requested = False
            self._stop_event.clear()

    def _begin_operation(self, kind: str) -> str:
        """Acquire exclusive ownership of the shared generation runtime."""
        with self._operation_condition:
            if self._closing:
                raise RuntimeError(
                    "Generator is unloading; new operations are not accepted"
                )
            if not self._loaded:
                raise RuntimeError("Generator not loaded; call load() first")
            if self._active_operations:
                active_kind = next(iter(self._active_operations.values()))
                raise GeneratorOperationBusyError(
                    f"Generator is busy with active {active_kind} operation; "
                    f"cannot start concurrent {kind} operation"
                )
            self._stop_requested = False
            self._stop_event.clear()
            operation_id = uuid4().hex
            self._active_operations[operation_id] = kind
            return operation_id

    def _end_operation(self, operation_id: str) -> None:
        """Release one operation and wake a teardown waiting on exact owners."""
        with self._operation_condition:
            if self._active_operations.pop(operation_id, None) is None:
                raise RuntimeError("Generator operation ownership was already released")
            self._operation_condition.notify_all()

    def _request_operation_stop(self, operation_id: str) -> bool:
        """Cancel only while the named request still owns the generator."""
        with self._operation_condition:
            if operation_id not in self._active_operations:
                return False
            self._stop_requested = True
            self._stop_event.set()
            return True

    def unload(self, timeout: float = 2.0) -> bool:
        """Release local model components after failed startup or explicit teardown."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._operation_condition:
            self._closing = True
            self._stop_requested = True
            self._stop_event.set()
            while self._active_operations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "Generator unload retained components because operations "
                        "are still active: %s",
                        sorted(self._active_operations.values()),
                    )
                    return False
                self._operation_condition.wait(remaining)

        stop_health_monitor = getattr(self.sequential, "stop_health_monitor", None)
        if callable(stop_health_monitor):
            try:
                stopped = stop_health_monitor(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except TypeError:
                stopped = stop_health_monitor()
            if stopped is False:
                logger.warning(
                    "Generator unload retained components because its provider "
                    "health monitor is still stopping"
                )
                return False
        with self._operation_condition:
            self._loaded = False
        if self._model_shell_pruned and self._component_cache_key is not None:
            try:
                self._store_cached_components(self._component_cache_key)
            except RuntimeError:
                logger.warning("Could not preserve incomplete generator component cache")
        self.tokenizer = None
        self.embed_tokens = None
        self.position_embeddings = None
        self.norm = None
        self.lm_head = None
        self.architecture_adapter = None
        self._loaded_model = None
        with self._reference_lock:
            self._release_reference_model()
        self._model_shell_pruned = False
        self._component_cache_key = None
        self._last_generation_metrics = None
        torch.cuda.empty_cache()
        return True

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate_stream(
        self,
        prompt:         str,
        max_new_tokens: Optional[int]   = None,
        temperature:    Optional[float] = None,
        top_p:          Optional[float] = None,
        top_k:          Optional[int]   = None,
        repetition_penalty: Optional[float] = None,
        do_sample:      Optional[bool]  = None,
    ) -> AsyncGenerator[dict, None]:
        operation_id = self._begin_operation("generate")
        completed = False
        try:
            owned_stream = self._generate_stream_owned(
                prompt=prompt,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
            )
            async with aclosing(owned_stream):
                async for chunk in owned_stream:
                    yield chunk
            completed = True
        finally:
            if not completed:
                self._request_operation_stop(operation_id)
            self._end_operation(operation_id)

    async def _generate_stream_owned(
        self,
        prompt:         str,
        max_new_tokens: Optional[int]   = None,
        temperature:    Optional[float] = None,
        top_p:          Optional[float] = None,
        top_k:          Optional[int]   = None,
        repetition_penalty: Optional[float] = None,
        do_sample:      Optional[bool]  = None,
    ) -> AsyncGenerator[dict, None]:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        session_started = False
        start_session = getattr(self.sequential, "start_session", None)
        if callable(start_session):
            start_session(str(uuid4()))
            session_started = True
        generation_started_at = time.perf_counter()
        first_token_at: Optional[float] = None
        generated_token_count = 0
        route_validation_ms_total = 0.0
        route_attempts = 0
        failed_over = False
        failover_reasons: list[dict] = []
        route_revision: Optional[str] = None
        hop_totals: dict[tuple[str, int, int], dict] = {}
        cfg = self._get_gen_config()
        logger.info(
            "[gen] starting generation prompt=%r max_new_tokens=%s temperature=%s top_p=%s",
            prompt,
            max_new_tokens if max_new_tokens is not None else cfg["max_new_tokens"],
            temperature if temperature is not None else cfg["temperature"],
            top_p if top_p is not None else cfg["top_p"],
        )

        # caller overrides win; otherwise fall back to per-model defaults
        max_new_tokens = max_new_tokens if max_new_tokens is not None else cfg["max_new_tokens"]
        temperature    = temperature    if temperature    is not None else cfg["temperature"]
        top_p          = top_p          if top_p          is not None else cfg["top_p"]
        top_k          = top_k          if top_k          is not None else cfg["top_k"]
        rep_penalty    = (
            repetition_penalty
            if repetition_penalty is not None
            else cfg["repetition_penalty"]
        )
        do_sample      = do_sample      if do_sample      is not None else True


        try:
            input_ids     = self._encode_prompt(prompt)
            generated_ids = input_ids.clone()
            stream_decoder = _ContextAwareTextDecoder(self.tokenizer)
            node_trace:   list[str] = []
            logger.debug(
                "[gen] prompt tokenized to ids shape=%s eos_token_id=%s",
                tuple(input_ids.shape),
                self.tokenizer.eos_token_id,
            )

            for step in range(max_new_tokens):
                if self._stop_requested:
                    logger.info("[gen] stop requested before step=%s", step)
                    break

                position_ids = torch.arange(
                    generated_ids.shape[1],
                    device=self.device,
                    dtype=torch.long,
                ).unsqueeze(0)

                attention_mask = torch.ones(
                    generated_ids.shape,
                    device=self.device,
                    dtype=torch.bool,
                )

                with torch.no_grad():
                    hidden_states = self._prepare_hidden_states(
                        generated_ids,
                        attention_mask,
                        position_ids,
                    )

                logger.debug(
                    "[gen] step=%s input_shape=%s attention_shape=%s position_shape=%s",
                    step,
                    tuple(generated_ids.shape),
                    tuple(attention_mask.shape),
                    tuple(position_ids.shape),
                )
                self._validate_generation_inputs(
                    input_ids=generated_ids,
                    hidden_states=hidden_states,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                )

                try:
                    hidden_states, trace = await self._forward_async(
                        hidden_states=hidden_states,
                        attention_mask=attention_mask,
                        position_ids=position_ids,
                    )
                except RouteCancellationError:
                    logger.info("[gen] cancellation stopped active route work")
                    break

                forward_metrics_getter = getattr(
                    self.sequential,
                    "get_last_forward_metrics",
                    None,
                )
                if callable(forward_metrics_getter):
                    forward_metrics = forward_metrics_getter()
                    route_validation_ms_total += float(
                        forward_metrics.get("route_validation_ms", 0.0)
                    )
                    route_attempts += int(forward_metrics.get("attempt_count", 1))
                    failed_over = failed_over or bool(
                        forward_metrics.get("failed_over", False)
                    )
                    failover_reasons.extend(
                        dict(reason)
                        for reason in forward_metrics.get("failover_reasons", [])
                    )
                    route_revision = forward_metrics.get("route_revision", route_revision)
                    for hop in forward_metrics.get("hops", []):
                        key = (
                            str(hop.get("peer_id", "unknown")),
                            int(hop.get("layer_start", 0)),
                            int(hop.get("layer_end", 0)),
                        )
                        aggregate = hop_totals.setdefault(
                            key,
                            {
                                "peer_id": key[0],
                                "selected_peer_id": str(
                                    hop.get("selected_peer_id", key[0])
                                ),
                                "executed_peer_id": str(
                                    hop.get("executed_peer_id", key[0])
                                ),
                                "rpc_uid": str(hop.get("rpc_uid", "")),
                                "layer_start": key[1],
                                "layer_end": key[2],
                                "calls": 0,
                                "total_latency_ms": 0.0,
                                "last_latency_ms": 0.0,
                            },
                        )
                        latency_ms = float(hop.get("latency_ms", 0.0))
                        aggregate["calls"] += 1
                        aggregate["total_latency_ms"] += latency_ms
                        aggregate["last_latency_ms"] = latency_ms

                node_trace = trace
                if self._stop_requested:
                    logger.info("[gen] stop requested after route step=%s", step)
                    break

                hidden_states = hidden_states.to(self.device)

                hidden_states = hidden_states.to(self.dtype)

                with torch.no_grad():
                    hidden_states = self.norm(hidden_states)
                    logits        = self.lm_head(hidden_states)

                next_token_logits = logits[:, -1, :]
                if next_token_logits.dim() != 2:
                    raise RuntimeError(
                        f"Expected [batch, vocab], got {next_token_logits.shape}"
                    )
                logger.debug(
                    "[gen] step=%s logits_shape=%s",
                    step,
                    tuple(next_token_logits.shape),
                )
                next_token_id     = self._sample(
                    next_token_logits,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repetition_penalty=rep_penalty,
                    do_sample=do_sample,
                    generated_ids=generated_ids,
                )

                if next_token_id.shape != (1, 1):
                    raise RuntimeError(f"Expected shape (1,1), got {next_token_id.shape}")
                generated_token_count += 1
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                generated_ids = torch.cat([generated_ids, next_token_id], dim=1)
                token_text = stream_decoder.push(int(next_token_id.item()))
                if token_text:
                    yield {"token": token_text}

                if next_token_id.item() == self.tokenizer.eos_token_id:
                    logger.debug(f"EOS at step {step}")
                    break

            final_text = stream_decoder.finish()
            if final_text:
                yield {"token": final_text}

            completed_at = time.perf_counter()
            total_duration_ms = (completed_at - generation_started_at) * 1000
            elapsed_seconds = max(completed_at - generation_started_at, 1e-9)
            hop_metrics = []
            for aggregate in hop_totals.values():
                calls = int(aggregate["calls"])
                hop_metrics.append(
                    {
                        **aggregate,
                        "average_latency_ms": (
                            float(aggregate["total_latency_ms"]) / calls
                            if calls
                            else 0.0
                        ),
                    }
                )
            metrics = {
                "time_to_first_token_ms": (
                    (first_token_at - generation_started_at) * 1000
                    if first_token_at is not None
                    else None
                ),
                "total_duration_ms": total_duration_ms,
                "generated_tokens": generated_token_count,
                "tokens_per_second": generated_token_count / elapsed_seconds,
                "route_validation_ms_total": route_validation_ms_total,
                "stopped": self._stop_requested,
                "hop_metrics": hop_metrics,
                "route_attempts": route_attempts,
                "failed_over": failed_over,
                "failover_reasons": failover_reasons,
                "route_revision": route_revision,
            }
            self._last_generation_metrics = metrics
            yield {
                "done": True,
                "node_trace": node_trace,
                "metrics": copy.deepcopy(metrics),
            }

        except Exception as e:
            logger.error(f"Generation error: {e}", exc_info=True)
            yield {"error": str(e)}
        finally:
            end_session = getattr(self.sequential, "end_session", None)
            if session_started and callable(end_session):
                end_session()

    async def compare_generated_output(
        self,
        prompt: str,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        do_sample: Optional[bool] = None,
    ) -> dict:
        operation_id = self._begin_operation("generated_output_parity")
        try:
            return await self._compare_generated_output_owned(
                prompt=prompt,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
            )
        finally:
            self._end_operation(operation_id)

    async def _compare_generated_output_owned(
        self,
        prompt: str,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        do_sample: Optional[bool] = None,
    ) -> dict:
        """
        Compare direct HuggingFace generation against the distributed route.

        Greedy generation is the intended whole-output parity mode. Sampled
        generation can still be compared qualitatively, but stochastic outputs
        should not be treated as an exact route-correctness signal.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if self._loaded_model is None:
            raise RuntimeError("Direct HuggingFace reference model is not loaded")

        cfg = self._get_gen_config()
        max_new_tokens = max_new_tokens if max_new_tokens is not None else cfg["max_new_tokens"]
        temperature = temperature if temperature is not None else cfg["temperature"]
        top_p = top_p if top_p is not None else cfg["top_p"]
        top_k = top_k if top_k is not None else cfg["top_k"]
        rep_penalty = (
            repetition_penalty
            if repetition_penalty is not None
            else cfg["repetition_penalty"]
        )
        do_sample = do_sample if do_sample is not None else False

        direct_response, direct_generated_ids = await asyncio.to_thread(
            self._generate_direct_text,
            prompt=prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=rep_penalty,
            do_sample=do_sample,
        )

        distributed_response = ""
        node_trace: list[str] = []
        generation_metrics: Optional[dict] = None
        async for chunk in self._generate_stream_owned(
            prompt=prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=rep_penalty,
            do_sample=do_sample,
        ):
            if "token" in chunk:
                distributed_response += chunk["token"]
            elif "done" in chunk:
                node_trace = chunk.get("node_trace", [])
                generation_metrics = chunk.get("metrics")
            elif "error" in chunk:
                raise RuntimeError(chunk["error"])

        return {
            "prompt": prompt,
            "model_name": self.model_name,
            "generation_config": {
                "max_new_tokens": max_new_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "repetition_penalty": rep_penalty,
                "do_sample": do_sample,
            },
            "direct_response": direct_response,
            "distributed_response": distributed_response,
            "exact_text_match": direct_response == distributed_response,
            "direct_generated_token_ids": direct_generated_ids,
            "direct_response_contains_replacement_char": "\ufffd" in direct_response,
            "distributed_response_contains_replacement_char": "\ufffd"
            in distributed_response,
            "node_trace": node_trace,
            "performance": copy.deepcopy(generation_metrics),
            "interpretation": (
                "Exact text match is meaningful for greedy generation. "
                "Sampled generation can diverge without proving a route bug."
            ),
        }

    def compare_next_token_logits(
        self,
        prompt: str,
        atol: float = 1e-4,
        rtol: float = 1e-4,
    ) -> dict:
        operation_id = self._begin_operation("next_token_parity")
        try:
            return self._compare_next_token_logits_owned(
                prompt=prompt,
                atol=atol,
                rtol=rtol,
            )
        finally:
            self._end_operation(operation_id)

    def _compare_next_token_logits_owned(
        self,
        prompt: str,
        atol: float = 1e-4,
        rtol: float = 1e-4,
    ) -> dict:
        """
        Deterministic parity probe for Sprint 07.

        Compares the direct HuggingFace next-token logits against the
        distributed route for the same prompt. This bypasses sampling so bad
        prose can be classified separately from route/model mismatch.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        with self._reference_lock:
            reference_model = self._get_reference_model()
            reference_device = self._prepare_reference_model_for_parity()
            input_ids = self._encode_prompt(prompt, device=reference_device)
            attention_mask = torch.ones(
                input_ids.shape,
                device=reference_device,
                dtype=torch.long,
            )
            reference_input_ids = input_ids.to(reference_device)

            try:
                with torch.no_grad():
                    reference_output = reference_model(
                        input_ids=reference_input_ids,
                        attention_mask=attention_mask,
                    )
                    reference_logits = (
                        reference_output.logits[:, -1, :].detach().cpu().float()
                    )
            finally:
                self._release_reference_model()

        distributed_input_ids = input_ids.to(self.device)
        distributed_attention_mask = torch.ones(
            distributed_input_ids.shape,
            device=self.device,
            dtype=torch.bool,
        )
        distributed_position_ids = torch.arange(
            distributed_input_ids.shape[1],
            device=self.device,
            dtype=torch.long,
        ).unsqueeze(0)

        with torch.no_grad():
            hidden_states = self._prepare_hidden_states(
                distributed_input_ids,
                distributed_attention_mask,
                distributed_position_ids,
            )

        self._validate_generation_inputs(
            input_ids=distributed_input_ids,
            hidden_states=hidden_states,
            attention_mask=distributed_attention_mask,
            position_ids=distributed_position_ids,
        )
        hidden_states, node_trace = self.sequential.forward(
            hidden_states=hidden_states,
            attention_mask=distributed_attention_mask,
            position_ids=distributed_position_ids,
        )
        hidden_states = hidden_states.to(self.device, dtype=self.dtype)

        with torch.no_grad():
            hidden_states = self.norm(hidden_states)
            distributed_logits = self.lm_head(hidden_states)[:, -1, :].detach().cpu().float()

        if reference_logits.shape != distributed_logits.shape:
            raise RuntimeError(
                "Parity logits shape mismatch: "
                f"direct={tuple(reference_logits.shape)} "
                f"distributed={tuple(distributed_logits.shape)}"
            )

        diff = (reference_logits - distributed_logits).abs()
        direct_token_id = int(reference_logits.argmax(dim=-1).item())
        distributed_token_id = int(distributed_logits.argmax(dim=-1).item())

        return {
            "prompt": prompt,
            "model_name": self.model_name,
            "logits_shape": list(reference_logits.shape),
            "direct_next_token_id": direct_token_id,
            "direct_next_token_text": self.tokenizer.decode(
                [direct_token_id],
                skip_special_tokens=True,
            ),
            "distributed_next_token_id": distributed_token_id,
            "distributed_next_token_text": self.tokenizer.decode(
                [distributed_token_id],
                skip_special_tokens=True,
            ),
            "argmax_match": direct_token_id == distributed_token_id,
            "max_abs_diff": float(diff.max().item()),
            "mean_abs_diff": float(diff.mean().item()),
            "allclose": bool(
                torch.allclose(
                    reference_logits,
                    distributed_logits,
                    atol=atol,
                    rtol=rtol,
                )
            ),
            "atol": atol,
            "rtol": rtol,
            "node_trace": node_trace,
        }

    def _generate_direct_text(
        self,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
        do_sample: bool,
    ) -> tuple[str, list[int]]:
        with self._reference_lock:
            reference_model = self._get_reference_model()
            reference_device = self._prepare_reference_model_for_parity()
            input_ids = self._encode_prompt(prompt, device=reference_device)
            attention_mask = torch.ones(
                input_ids.shape,
                device=reference_device,
                dtype=torch.long,
            )

            generate_kwargs = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "max_new_tokens": max_new_tokens,
                "do_sample": do_sample,
                "repetition_penalty": repetition_penalty,
            }
            if getattr(self.tokenizer, "pad_token_id", None) is not None:
                generate_kwargs["pad_token_id"] = self.tokenizer.pad_token_id
            if getattr(self.tokenizer, "eos_token_id", None) is not None:
                generate_kwargs["eos_token_id"] = self.tokenizer.eos_token_id
            if do_sample:
                generate_kwargs.update(
                    {
                        "temperature": temperature,
                        "top_p": top_p,
                        "top_k": top_k,
                    }
                )

            try:
                with torch.no_grad():
                    output_ids = reference_model.generate(**generate_kwargs)
            finally:
                self._release_reference_model()

        generated_ids = output_ids[0][input_ids.shape[1] :].detach().cpu().tolist()
        generated_ids = [int(token_id) for token_id in generated_ids]
        response = self.tokenizer.decode(
            generated_ids,
            skip_special_tokens=True,
        )
        return response, generated_ids

    def trace_generation(
        self,
        prompt: str,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        do_sample: Optional[bool] = None,
    ) -> dict:
        operation_id = self._begin_operation("trace")
        try:
            return self._trace_generation_owned(
                prompt=prompt,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
            )
        finally:
            self._end_operation(operation_id)

    def _trace_generation_owned(
        self,
        prompt: str,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        repetition_penalty: Optional[float] = None,
        do_sample: Optional[bool] = None,
    ) -> dict:
        """
        Diagnostic generation path that records token-level state.

        This is intentionally synchronous and non-streaming so debugging can
        inspect exactly which token introduced odd text or replacement chars.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        cfg = self._get_gen_config()
        max_new_tokens = max_new_tokens if max_new_tokens is not None else cfg["max_new_tokens"]
        temperature = temperature if temperature is not None else cfg["temperature"]
        top_p = top_p if top_p is not None else cfg["top_p"]
        top_k = top_k if top_k is not None else cfg["top_k"]
        rep_penalty = (
            repetition_penalty
            if repetition_penalty is not None
            else cfg["repetition_penalty"]
        )
        do_sample = do_sample if do_sample is not None else True

        generated_ids = self._encode_prompt(prompt)
        prompt_token_ids = [int(token_id) for token_id in generated_ids[0].tolist()]
        generated_token_ids: list[int] = []
        steps: list[dict] = []
        node_trace: list[str] = []
        decoded_output = ""

        for step in range(max_new_tokens):
            if self._stop_requested:
                logger.info("[trace] stop requested before step=%s", step)
                break
            position_ids = torch.arange(
                generated_ids.shape[1],
                device=self.device,
                dtype=torch.long,
            ).unsqueeze(0)
            attention_mask = torch.ones(
                generated_ids.shape,
                device=self.device,
                dtype=torch.bool,
            )

            with torch.no_grad():
                hidden_states = self._prepare_hidden_states(
                    generated_ids,
                    attention_mask,
                    position_ids,
                )

            self._validate_generation_inputs(
                input_ids=generated_ids,
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )
            hidden_shape_before_route = list(hidden_states.shape)
            hidden_states, node_trace = self.sequential.forward(
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )
            if self._stop_requested:
                logger.info("[trace] stop requested after route step=%s", step)
                break
            hidden_shape_after_route = list(hidden_states.shape)
            hidden_states = hidden_states.to(self.device, dtype=self.dtype)

            with torch.no_grad():
                hidden_states = self.norm(hidden_states)
                logits = self.lm_head(hidden_states)

            next_token_logits = logits[:, -1, :]
            top_values, top_indices = torch.topk(
                next_token_logits.detach().cpu().float(),
                k=min(5, next_token_logits.shape[-1]),
                dim=-1,
            )
            next_token_id = self._sample(
                next_token_logits.clone(),
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=rep_penalty,
                do_sample=do_sample,
                generated_ids=generated_ids,
            )
            if next_token_id.shape != (1, 1):
                raise RuntimeError(f"Expected shape (1,1), got {next_token_id.shape}")

            token_id = int(next_token_id.item())
            token_text = self.tokenizer.decode(
                next_token_id[0],
                skip_special_tokens=True,
            )
            selected_in_top_candidates = token_id in top_indices[0].tolist()
            generated_ids = torch.cat([generated_ids, next_token_id], dim=1)
            generated_token_ids.append(token_id)
            decoded_output = self.tokenizer.decode(
                generated_token_ids,
                skip_special_tokens=True,
            )

            steps.append(
                {
                    "step": step,
                    "context_length": int(generated_ids.shape[1] - 1),
                    "input_token_ids_before_selection": [
                        int(existing_token_id)
                        for existing_token_id in generated_ids[0][:-1].tolist()
                    ],
                    "attention_mask_shape": list(attention_mask.shape),
                    "position_ids": [
                        int(position_id)
                        for position_id in position_ids[0].tolist()
                    ],
                    "hidden_shape_before_route": hidden_shape_before_route,
                    "hidden_shape_after_route": hidden_shape_after_route,
                    "logits_shape": list(next_token_logits.shape),
                    "token_id": token_id,
                    "token_text": token_text,
                    "token_text_contains_replacement_char": "\ufffd" in token_text,
                    "selected_in_top_candidates": selected_in_top_candidates,
                    "decoded_output_so_far": decoded_output,
                    "decoded_output_contains_replacement_char": "\ufffd" in decoded_output,
                    "top_candidates": [
                        {
                            "token_id": int(candidate_id),
                            "token_text": self.tokenizer.decode(
                                [int(candidate_id)],
                                skip_special_tokens=True,
                            ),
                            "token_text_contains_replacement_char": (
                                "\ufffd"
                                in self.tokenizer.decode(
                                    [int(candidate_id)],
                                    skip_special_tokens=True,
                                )
                            ),
                            "logit": float(candidate_logit),
                        }
                        for candidate_id, candidate_logit in zip(
                            top_indices[0].tolist(),
                            top_values[0].tolist(),
                        )
                    ],
                }
            )

            if token_id == self.tokenizer.eos_token_id:
                break

        return {
            "prompt": prompt,
            "model_name": self.model_name,
            "generation_config": {
                "max_new_tokens": max_new_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "repetition_penalty": rep_penalty,
                "do_sample": do_sample,
            },
            "tokenizer_class": type(self.tokenizer).__name__,
            "device": str(self.device),
            "dtype": str(self.dtype),
            "prompt_token_ids": prompt_token_ids,
            "prompt_tokens": [
                {
                    "token_id": token_id,
                    "token_text": self.tokenizer.decode(
                        [token_id],
                        skip_special_tokens=True,
                    ),
                    "token_text_contains_replacement_char": (
                        "\ufffd"
                        in self.tokenizer.decode(
                            [token_id],
                            skip_special_tokens=True,
                        )
                    ),
                }
                for token_id in prompt_token_ids
            ],
            "response": decoded_output,
            "response_contains_replacement_char": "\ufffd" in decoded_output,
            "steps": steps,
            "node_trace": node_trace,
        }

    def _prepare_hidden_states(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        if self.architecture_adapter is not None and self._loaded_model is not None:
            return self.architecture_adapter.prepare_inputs(
                model=self._loaded_model,
                input_ids=input_ids,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )

        if self.embed_tokens is None:
            raise RuntimeError("embed_tokens is not loaded")
        hidden_states = self.embed_tokens(input_ids)

        if self.position_embeddings is not None:
            position_embeddings = self.position_embeddings(position_ids)
            hidden_states = hidden_states + position_embeddings

        return hidden_states

    async def _forward_async(
        self,
        *,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> tuple[torch.Tensor, list[str]]:
        forward = self.sequential.forward
        parameters = inspect.signature(forward).parameters.values()
        supports_cancel = any(
            parameter.name == "cancel_event"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        kwargs = {
            "hidden_states": hidden_states,
            "attention_mask": attention_mask,
            "position_ids": position_ids,
        }
        if supports_cancel:
            kwargs["cancel_event"] = self._stop_event
        return await asyncio.to_thread(forward, **kwargs)

    def _prepare_reference_model_for_parity(self) -> torch.device:
        model = self._get_reference_model()
        if model is None:
            raise RuntimeError("Direct HuggingFace reference model is not loaded")
        reference_device = torch.device(self.device)
        model.to(reference_device)
        model.eval()
        return reference_device

    def _get_reference_model(self) -> nn.Module:
        if self._reference_model is not None:
            return self._reference_model
        if self._loaded_model is not None and not self._model_shell_pruned:
            return self._loaded_model
        source = self.local_model_path or self.model_name
        token_kwargs = (
            {"token": self.hf_token}
            if self.hf_token and not self.local_model_path
            else ({"token": False} if not self.local_model_path else {})
        )
        local_kwargs = {"local_files_only": True} if self.local_model_path else {}
        self._reference_model = AutoModelForCausalLM.from_pretrained(
            source,
            torch_dtype=self.dtype,
            low_cpu_mem_usage=True,
            device_map="cpu",
            **token_kwargs,
            **local_kwargs,
        )
        self._reference_model.eval()
        return self._reference_model

    def _release_reference_model(self) -> None:
        if self._reference_model is None:
            return
        self._reference_model.to("cpu")
        self._reference_model = None
        torch.cuda.empty_cache()

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _sample(
        self,
        logits:             torch.Tensor,
        temperature:        float = 0.8,
        top_p:              float = 0.92,
        top_k:              int   = 50,
        repetition_penalty: float = 1.1,
        do_sample:          bool  = True,
        generated_ids:      Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        if logits.dim() != 2:
            raise ValueError(f"Expected [1, vocab], got {logits.shape}")

        # 1. Repetition penalty — penalise tokens already in the sequence
        if repetition_penalty != 1.0 and generated_ids is not None:
            for token_id in set(generated_ids[0].tolist()):
                if logits[0, token_id] < 0:
                    logits[0, token_id] *= repetition_penalty
                else:
                    logits[0, token_id] /= repetition_penalty

        if not do_sample:
            return torch.argmax(logits, dim=-1, keepdim=True)

        # 2. Temperature
        if temperature > 0:
            logits = logits / temperature

        # 3. Top-k — zero out everything outside top k logits
        if top_k > 0:
            top_k_values, _ = torch.topk(logits, min(top_k, logits.size(-1)))
            logits[logits < top_k_values[:, -1].unsqueeze(-1)] = float('-inf')

        # 4. Top-p (nucleus sampling)
        probs = torch.softmax(logits, dim=-1)

        if top_p < 1.0:
            sorted_probs, sorted_indices = torch.sort(probs, descending=True)
            cumulative = torch.cumsum(sorted_probs, dim=-1)
            sorted_probs[(cumulative - sorted_probs) > top_p] = 0.0
            total = sorted_probs.sum(dim=-1, keepdim=True)
            if torch.any(total <= 0):
                raise RuntimeError("All probabilities zeroed out in top-p filtering")
            sorted_probs = sorted_probs / total
            sampled      = torch.multinomial(sorted_probs, num_samples=1)
            next_token   = sorted_indices.gather(-1, sampled)
        else:
            next_token = torch.argmax(probs, dim=-1, keepdim=True)

        return next_token

    # ------------------------------------------------------------------
    # Architecture helpers
    # ------------------------------------------------------------------

    def _extract_embed_tokens(self, model: nn.Module) -> nn.Embedding:
        if hasattr(model, "model") and hasattr(model.model, "embed_tokens"):
            return model.model.embed_tokens
        if hasattr(model, "transformer") and hasattr(model.transformer, "word_embeddings"):
            return model.transformer.word_embeddings
        if hasattr(model, "model") and hasattr(model.model, "decoder"):
            return model.model.decoder.embed_tokens
        raise ValueError(f"Cannot find embed_tokens in {type(model).__name__}")

    def _extract_position_embeddings(self, model: nn.Module) -> Optional[nn.Embedding]:
        if hasattr(model, "model") and hasattr(model.model, "decoder"):
            decoder = model.model.decoder
            if hasattr(decoder, "embed_positions"):
                return decoder.embed_positions
        if hasattr(model, "transformer") and hasattr(model.transformer, "position_embeddings"):
            return model.transformer.position_embeddings
        return None

    def _extract_norm(self, model: nn.Module) -> nn.Module:
        if hasattr(model, "model") and hasattr(model.model, "norm"):
            return model.model.norm
        if hasattr(model, "transformer") and hasattr(model.transformer, "ln_f"):
            return model.transformer.ln_f
        if hasattr(model, "model") and hasattr(model.model, "decoder") \
                and hasattr(model.model.decoder, "final_layer_norm"):
            return model.model.decoder.final_layer_norm
        logger.warning(f"Cannot find final norm in {type(model).__name__} — using Identity")
        return nn.Identity()

    def _extract_lm_head(self, model: nn.Module) -> nn.Linear:
        if hasattr(model, "lm_head"):
            return model.lm_head
        raise ValueError(f"Cannot find lm_head in {type(model).__name__}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._loaded

    def request_stop(self) -> None:
        with self._operation_condition:
            self._stop_requested = True
            self._stop_event.set()

    def clear_stop(self) -> bool:
        with self._operation_condition:
            if self._closing:
                return False
            self._stop_requested = False
            self._stop_event.clear()
            return True
