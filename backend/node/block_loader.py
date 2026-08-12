"""Memory-efficient loading for a worker's transformer layer range."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil
import torch
import torch.nn as nn
from accelerate import init_empty_weights
from accelerate.utils import set_module_tensor_to_device
from hivemind.utils.logging import get_logger
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, LocalEntryNotFoundError
from safetensors import safe_open
from transformers import AutoConfig, AutoModelForCausalLM

logger = get_logger(__name__)


class SelectiveLoadError(RuntimeError):
    """Raised when a checkpoint cannot be loaded without materializing the model."""


class SelectiveLoadUnsupportedError(SelectiveLoadError):
    """Raised when the checkpoint requires the explicit compatibility fallback."""


@dataclass(frozen=True)
class SelectiveLoadPlan:
    model_revision: str
    architecture: str
    weight_format: str
    layer_start: int
    layer_end: int
    layer_prefix: str
    tensor_files: dict[str, str]
    source_shards: tuple[str, ...]
    checkpoint_total_bytes: int | None


@dataclass(frozen=True)
class LoadDiagnostics:
    strategy: str
    architecture: str
    weight_format: str
    model_revision: str
    layer_start: int
    layer_end: int
    loaded_parameter_count: int
    loaded_parameter_bytes: int
    selected_checkpoint_bytes: int
    checkpoint_total_bytes: int | None
    source_shard_count: int
    source_shards: tuple[str, ...]
    elapsed_seconds: float
    baseline_rss_bytes: int
    peak_rss_bytes: int
    peak_rss_delta_bytes: int
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _RSSProbe:
    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self.baseline = self._process.memory_info().rss
        self.peak = self.baseline
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.01):
            self.peak = max(self.peak, self._process.memory_info().rss)

    def __enter__(self) -> _RSSProbe:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.peak = max(self.peak, self._process.memory_info().rss)
        self._stop.set()
        self._thread.join(timeout=0.2)


def load_layers(
    model_name: str,
    layer_start: int,
    layer_end: int,
    device: str = "cuda",
    dtype: torch.dtype = torch.float16,
    hf_token: str | None = None,
    local_model_path: str | None = None,
) -> nn.ModuleList:
    """Load exactly ``[layer_start, layer_end)`` from a safetensors checkpoint."""
    if not model_name.strip():
        raise ValueError("model_name must not be empty")
    if layer_start < 0:
        raise ValueError(f"layer_start must be >= 0, got {layer_start}")
    if layer_end <= layer_start:
        raise ValueError(
            f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
        )
    if device not in {"cuda", "cpu"}:
        raise ValueError(f"device must be 'cuda' or 'cpu', got {device!r}")

    source = local_model_path or model_name
    token = None if local_model_path else (hf_token if hf_token else False)
    config = AutoConfig.from_pretrained(
        source,
        token=token,
        local_files_only=bool(local_model_path),
    )
    num_layers = _get_num_layers(config)
    if layer_end > num_layers:
        raise ValueError(f"layer_end ({layer_end}) exceeds model depth ({num_layers})")

    try:
        plan = _build_selective_plan(
            source=source,
            config=config,
            layer_start=layer_start,
            layer_end=layer_end,
            token=token,
            local_only=bool(local_model_path),
        )
    except SelectiveLoadUnsupportedError as exc:
        if not _full_model_fallback_enabled():
            raise SelectiveLoadError(
                f"{exc} Full-model fallback is disabled by "
                "DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK=false."
            ) from exc
        return _load_full_model_fallback(
            model_name=model_name,
            source=source,
            config=config,
            layer_start=layer_start,
            layer_end=layer_end,
            device=device,
            dtype=dtype,
            token=token,
            local_only=bool(local_model_path),
            reason=str(exc),
        )
    started_at = time.perf_counter()
    with _RSSProbe() as memory:
        layers, parameter_count, parameter_bytes = _materialize_layers(
            config=config,
            plan=plan,
            device=device,
            dtype=dtype,
        )

    selected_checkpoint_bytes = sum(
        os.path.getsize(path) for path in set(plan.tensor_files.values())
    )
    diagnostics = LoadDiagnostics(
        strategy="selective_safetensors",
        architecture=plan.architecture,
        weight_format=plan.weight_format,
        model_revision=plan.model_revision,
        layer_start=layer_start,
        layer_end=layer_end,
        loaded_parameter_count=parameter_count,
        loaded_parameter_bytes=parameter_bytes,
        selected_checkpoint_bytes=selected_checkpoint_bytes,
        checkpoint_total_bytes=plan.checkpoint_total_bytes,
        source_shard_count=len(plan.source_shards),
        source_shards=plan.source_shards,
        elapsed_seconds=time.perf_counter() - started_at,
        baseline_rss_bytes=memory.baseline,
        peak_rss_bytes=memory.peak,
        peak_rss_delta_bytes=max(0, memory.peak - memory.baseline),
    )
    layers.load_diagnostics = diagnostics.to_dict()
    logger.info(
        "Selective layer load complete | model=%s architecture=%s range=%s-%s "
        "parameters=%s bytes=%s peak_rss_delta=%s shards=%s elapsed=%.3fs",
        model_name,
        plan.architecture,
        layer_start,
        layer_end,
        parameter_count,
        parameter_bytes,
        diagnostics.peak_rss_delta_bytes,
        plan.source_shards,
        diagnostics.elapsed_seconds,
    )
    return layers


def _build_selective_plan(
    *,
    source: str,
    config: Any,
    layer_start: int,
    layer_end: int,
    token: str | bool | None,
    local_only: bool,
) -> SelectiveLoadPlan:
    architecture, layer_prefix = _architecture_contract(config)
    revision = str(getattr(config, "_commit_hash", None) or "local")
    source_path = Path(source).expanduser() if local_only else None

    if source_path is not None:
        index_path = source_path / "model.safetensors.index.json"
        single_path = source_path / "model.safetensors"
        if index_path.is_file():
            return _plan_from_index(
                index_path=index_path,
                shard_resolver=lambda name: _resolve_local_shard(source_path, name),
                architecture=architecture,
                revision=revision,
                layer_prefix=layer_prefix,
                layer_start=layer_start,
                layer_end=layer_end,
            )
        if single_path.is_file():
            return _plan_from_single_file(
                path=single_path,
                architecture=architecture,
                revision=revision,
                layer_prefix=layer_prefix,
                layer_start=layer_start,
                layer_end=layer_end,
            )
        _raise_unsupported_format(source_path)

    resolved_revision = None if revision == "local" else revision
    index_path = _find_cached_hf_file(
        source, "model.safetensors.index.json", resolved_revision, token
    )
    single_path = None
    if index_path is None:
        single_path = _find_cached_hf_file(
            source, "model.safetensors", resolved_revision, token
        )
    if index_path is None and single_path is None:
        cached_binary = _find_cached_hf_file(
            source, "pytorch_model.bin.index.json", resolved_revision, token
        ) or _find_cached_hf_file(source, "pytorch_model.bin", resolved_revision, token)
        if cached_binary is not None:
            raise SelectiveLoadUnsupportedError(
                "The cached repository snapshot contains PyTorch binary weights but no "
                "safetensors checkpoint."
            )
    if index_path is None and single_path is None:
        try:
            index_path = Path(
                hf_hub_download(
                    source,
                    "model.safetensors.index.json",
                    revision=resolved_revision,
                    token=token,
                    local_files_only=local_only,
                )
            )
        except EntryNotFoundError:
            try:
                single_path = Path(
                    hf_hub_download(
                        source,
                        "model.safetensors",
                        revision=resolved_revision,
                        token=token,
                        local_files_only=local_only,
                    )
                )
            except EntryNotFoundError as exc:
                raise SelectiveLoadUnsupportedError(
                    "The repository does not publish safetensors weights."
                ) from exc
    if single_path is not None:
        return _plan_from_single_file(
            path=single_path,
            architecture=architecture,
            revision=revision,
            layer_prefix=layer_prefix,
            layer_start=layer_start,
            layer_end=layer_end,
        )

    def resolve_remote_shard(name: str) -> str:
        cached = _find_cached_hf_file(source, name, resolved_revision, token)
        if cached is not None:
            return str(cached)
        return str(
            hf_hub_download(
                source,
                name,
                revision=resolved_revision,
                token=token,
                local_files_only=local_only,
            )
        )

    return _plan_from_index(
        index_path=index_path,
        shard_resolver=resolve_remote_shard,
        architecture=architecture,
        revision=revision,
        layer_prefix=layer_prefix,
        layer_start=layer_start,
        layer_end=layer_end,
    )


def _plan_from_index(
    *,
    index_path: Path,
    shard_resolver: Any,
    architecture: str,
    revision: str,
    layer_prefix: str,
    layer_start: int,
    layer_end: int,
) -> SelectiveLoadPlan:
    payload = _read_strict_json(index_path)
    weight_map = payload.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        raise SelectiveLoadError(
            f"Safetensors index {index_path.name} has no non-empty weight_map"
        )
    selected_names = _selected_checkpoint_names(
        weight_map.keys(), layer_prefix, layer_start, layer_end
    )
    if not selected_names:
        raise SelectiveLoadError(
            f"Checkpoint index contains no tensors for layers {layer_start}-{layer_end} "
            f"using prefix {layer_prefix!r}"
        )

    required_shards = sorted({weight_map[name] for name in selected_names})
    if not all(isinstance(name, str) and name.endswith(".safetensors") for name in required_shards):
        raise SelectiveLoadError("Selected checkpoint shards must all use safetensors")
    resolved = {name: shard_resolver(name) for name in required_shards}
    for name, path in resolved.items():
        if not Path(path).is_file():
            raise SelectiveLoadError(f"Safetensors shard is missing: {name}")

    metadata = payload.get("metadata")
    total_size = metadata.get("total_size") if isinstance(metadata, dict) else None
    return SelectiveLoadPlan(
        model_revision=revision,
        architecture=architecture,
        weight_format="safetensors_indexed",
        layer_start=layer_start,
        layer_end=layer_end,
        layer_prefix=layer_prefix,
        tensor_files={name: resolved[weight_map[name]] for name in selected_names},
        source_shards=tuple(required_shards),
        checkpoint_total_bytes=int(total_size) if isinstance(total_size, int) else None,
    )


def _plan_from_single_file(
    *,
    path: Path,
    architecture: str,
    revision: str,
    layer_prefix: str,
    layer_start: int,
    layer_end: int,
) -> SelectiveLoadPlan:
    try:
        with safe_open(path, framework="pt", device="cpu") as checkpoint:
            selected_names = _selected_checkpoint_names(
                checkpoint.keys(), layer_prefix, layer_start, layer_end
            )
    except Exception as exc:
        raise SelectiveLoadError(
            f"Could not inspect safetensors checkpoint {path.name}: {exc}"
        ) from exc
    if not selected_names:
        raise SelectiveLoadError(
            f"Checkpoint contains no tensors for layers {layer_start}-{layer_end} "
            f"using prefix {layer_prefix!r}"
        )
    return SelectiveLoadPlan(
        model_revision=revision,
        architecture=architecture,
        weight_format="safetensors_single",
        layer_start=layer_start,
        layer_end=layer_end,
        layer_prefix=layer_prefix,
        tensor_files={name: str(path) for name in selected_names},
        source_shards=(path.name,),
        checkpoint_total_bytes=path.stat().st_size,
    )


def _materialize_layers(
    *,
    config: Any,
    plan: SelectiveLoadPlan,
    device: str,
    dtype: torch.dtype,
) -> tuple[nn.ModuleList, int, int]:
    layer_class = _decoder_layer_class(plan.architecture)
    with init_empty_weights(include_buffers=True):
        layers = nn.ModuleList(
            [layer_class(config, index) for index in range(plan.layer_start, plan.layer_end)]
        )

    expected = layers.state_dict()
    source_for_module_key: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for relative_index, global_index in enumerate(range(plan.layer_start, plan.layer_end)):
        checkpoint_prefix = f"{plan.layer_prefix}{global_index}."
        module_prefix = f"{relative_index}."
        for module_key in (name for name in expected if name.startswith(module_prefix)):
            local_name = module_key[len(module_prefix):]
            checkpoint_key = checkpoint_prefix + local_name
            path = plan.tensor_files.get(checkpoint_key)
            if path is None:
                missing.append(checkpoint_key)
            else:
                source_for_module_key[module_key] = (checkpoint_key, path)
    if missing:
        preview = ", ".join(missing[:4])
        suffix = "..." if len(missing) > 4 else ""
        raise SelectiveLoadError(
            f"Checkpoint is missing {len(missing)} required layer tensors: {preview}{suffix}"
        )

    keys_by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for module_key, (checkpoint_key, path) in source_for_module_key.items():
        keys_by_file[path].append((module_key, checkpoint_key))

    parameter_count = 0
    parameter_bytes = 0
    for path, key_pairs in sorted(keys_by_file.items()):
        try:
            with safe_open(path, framework="pt", device="cpu") as checkpoint:
                available = set(checkpoint.keys())
                for module_key, checkpoint_key in key_pairs:
                    if checkpoint_key not in available:
                        raise SelectiveLoadError(
                            f"Safetensors shard {Path(path).name} does not contain indexed "
                            f"tensor {checkpoint_key}"
                        )
                    value = checkpoint.get_tensor(checkpoint_key)
                    expected_shape = tuple(expected[module_key].shape)
                    if tuple(value.shape) != expected_shape:
                        raise SelectiveLoadError(
                            f"Tensor {checkpoint_key} has shape {tuple(value.shape)}, "
                            f"expected {expected_shape}"
                        )
                    target_dtype = dtype if value.is_floating_point() else value.dtype
                    set_module_tensor_to_device(
                        layers,
                        module_key,
                        device,
                        value=value,
                        dtype=target_dtype,
                    )
                    parameter_count += value.numel()
                    parameter_bytes += value.numel() * torch.empty(
                        (), dtype=target_dtype
                    ).element_size()
        except SelectiveLoadError:
            raise
        except Exception as exc:
            raise SelectiveLoadError(
                f"Could not load selected tensors from {Path(path).name}: {exc}"
            ) from exc

    meta_tensors = [name for name, tensor in layers.state_dict().items() if tensor.is_meta]
    if meta_tensors:
        raise SelectiveLoadError(
            f"Selective load left {len(meta_tensors)} tensors unmaterialized: "
            + ", ".join(meta_tensors[:4])
        )
    layers.eval()
    return layers, parameter_count, parameter_bytes


def _architecture_contract(config: Any) -> tuple[str, str]:
    model_type = str(getattr(config, "model_type", "")).lower()
    if model_type == "opt":
        return "opt", "model.decoder.layers."
    if model_type == "llama":
        return "llama", "model.layers."
    if model_type == "mistral":
        return "mistral", "model.layers."
    raise SelectiveLoadUnsupportedError(
        f"Selective worker loading does not support model_type {model_type or 'unknown'!r}. "
        "Supported architectures are OPT, Llama, and Mistral."
    )


def _decoder_layer_class(architecture: str) -> type[nn.Module]:
    if architecture == "opt":
        from transformers.models.opt.modeling_opt import OPTDecoderLayer

        return OPTDecoderLayer
    if architecture == "llama":
        from transformers.models.llama.modeling_llama import LlamaDecoderLayer

        return LlamaDecoderLayer
    if architecture == "mistral":
        from transformers.models.mistral.modeling_mistral import MistralDecoderLayer

        return MistralDecoderLayer
    raise SelectiveLoadError(f"Unknown selective-load architecture: {architecture}")


def _selected_checkpoint_names(
    names: Any,
    layer_prefix: str,
    layer_start: int,
    layer_end: int,
) -> list[str]:
    prefixes = tuple(f"{layer_prefix}{index}." for index in range(layer_start, layer_end))
    return sorted(name for name in names if isinstance(name, str) and name.startswith(prefixes))


def _read_strict_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SelectiveLoadError(f"Duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except SelectiveLoadError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectiveLoadError(f"Could not read safetensors index {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SelectiveLoadError(f"Safetensors index {path.name} must contain a JSON object")
    return payload


def _raise_unsupported_format(source: Path) -> None:
    has_binary = (source / "pytorch_model.bin.index.json").is_file() or any(
        source.glob("*.bin")
    )
    if has_binary:
        raise SelectiveLoadUnsupportedError(
            "This worker checkpoint uses PyTorch binary weights. Selective loading requires "
            "model.safetensors or model.safetensors.index.json; download or convert the "
            "safetensors variant before serving layers."
        )
    raise SelectiveLoadUnsupportedError(
        "No supported worker checkpoint was found. Selective loading requires "
        "model.safetensors or model.safetensors.index.json."
    )


def _get_num_layers(config: Any) -> int:
    for attribute in ("num_hidden_layers", "n_layer", "num_layers"):
        value = getattr(config, attribute, None)
        if isinstance(value, int):
            return value
    raise SelectiveLoadError(
        f"Cannot determine layer count from config type {type(config).__name__}"
    )


def _find_cached_hf_file(
    repo_id: str,
    filename: str,
    revision: str | None,
    token: str | bool | None,
) -> Path | None:
    try:
        return Path(
            hf_hub_download(
                repo_id,
                filename,
                revision=revision,
                token=token,
                local_files_only=True,
            )
        )
    except LocalEntryNotFoundError:
        return None


def _resolve_local_shard(root: Path, name: str) -> str:
    root = root.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise SelectiveLoadError(
            f"Safetensors index references a shard outside the model directory: {name}"
        )
    return str(candidate)


def _full_model_fallback_enabled() -> bool:
    return os.getenv("DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_full_model_fallback(
    *,
    model_name: str,
    source: str,
    config: Any,
    layer_start: int,
    layer_end: int,
    device: str,
    dtype: torch.dtype,
    token: str | bool | None,
    local_only: bool,
    reason: str,
) -> nn.ModuleList:
    logger.warning(
        "Selective loading unavailable for %s; using full-model compatibility fallback: %s",
        model_name,
        reason,
    )
    started_at = time.perf_counter()
    with _RSSProbe() as memory:
        model = AutoModelForCausalLM.from_pretrained(
            source,
            config=config,
            dtype=dtype,
            low_cpu_mem_usage=True,
            device_map="cpu",
            token=token,
            local_files_only=local_only,
        )
        all_layers = _get_layers(model)
        layers = nn.ModuleList([all_layers[index] for index in range(layer_start, layer_end)])
        del all_layers
        del model
        layers = layers.to(device=device, dtype=dtype)
        layers.eval()

    parameter_count = sum(parameter.numel() for parameter in layers.parameters())
    parameter_bytes = sum(
        parameter.numel() * parameter.element_size() for parameter in layers.parameters()
    )
    diagnostics = LoadDiagnostics(
        strategy="full_model_fallback",
        architecture=str(getattr(config, "model_type", "unknown")),
        weight_format="unsupported_for_selective_loading",
        model_revision=str(getattr(config, "_commit_hash", None) or "local"),
        layer_start=layer_start,
        layer_end=layer_end,
        loaded_parameter_count=parameter_count,
        loaded_parameter_bytes=parameter_bytes,
        selected_checkpoint_bytes=0,
        checkpoint_total_bytes=None,
        source_shard_count=0,
        source_shards=(),
        elapsed_seconds=time.perf_counter() - started_at,
        baseline_rss_bytes=memory.baseline,
        peak_rss_bytes=memory.peak,
        peak_rss_delta_bytes=max(0, memory.peak - memory.baseline),
        fallback_reason=reason,
    )
    layers.load_diagnostics = diagnostics.to_dict()
    return layers


def _get_layers(model: nn.Module) -> nn.ModuleList:
    if hasattr(model, "model") and hasattr(model.model, "layers"):
        return model.model.layers
    if hasattr(model, "transformer") and hasattr(model.transformer, "h"):
        return model.transformer.h
    decoder = getattr(getattr(model, "model", None), "decoder", None)
    if decoder is not None and hasattr(decoder, "layers"):
        return decoder.layers
    raise SelectiveLoadError(
        f"Cannot locate transformer layers in fallback model {type(model).__name__}"
    )
