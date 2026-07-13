"""
block_loader.py
Downloads a HuggingFace model and loads only the specified
range of transformer layers into memory.

Supports:
    - Open models  (no token needed)
    - Gated models from a validated local directory
    - Llama, Mistral, Gemma, Qwen, Falcon, OPT, GPT-2 architectures
"""

import torch
import torch.nn as nn
from hivemind.utils.logging import get_logger
from transformers import AutoConfig, AutoModelForCausalLM

logger = get_logger(__name__)


def load_layers(
    model_name:  str,
    layer_start: int,
    layer_end:   int,
    device:      str = "cuda",
    dtype:       torch.dtype = torch.float16,
    hf_token:    str | None = None,
    local_model_path: str | None = None,
) -> nn.ModuleList:
    """
    Load transformer layers [layer_start, layer_end) from a HuggingFace model.

    Args:
        model_name:  HuggingFace model ID e.g. "facebook/opt-125m"
        layer_start: First layer index (inclusive)
        layer_end:   Last layer index (exclusive)
        device:      "cuda" or "cpu"
        dtype:       torch.float16 for GPU, torch.float32 for CPU
        hf_token:    Optional HuggingFace token fallback
        local_model_path: Validated local model directory for offline loading

    Returns:
        nn.ModuleList of transformer decoder layers ready for inference
    """
    assert model_name.strip(),        "model_name must not be empty"
    assert layer_start >= 0,          f"layer_start must be >= 0, got {layer_start}"
    assert layer_end > layer_start,   f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
    assert device in ("cuda", "cpu"), f"device must be 'cuda' or 'cpu', got '{device}'"

    source = local_model_path or model_name
    token_kwargs = (
        {"token": hf_token}
        if hf_token and not local_model_path
        else ({"token": False} if not local_model_path else {})
    )
    local_kwargs = {"local_files_only": True} if local_model_path else {}

    logger.info(f"Fetching config for {model_name} from {source}...")
    config = AutoConfig.from_pretrained(source, **token_kwargs, **local_kwargs)

    num_layers = _get_num_layers(config)
    assert layer_end <= num_layers, (
        f"layer_end ({layer_end}) exceeds model depth ({num_layers})"
    )

    logger.info(
        f"Model has {num_layers} layers total. "
        f"Loading layers {layer_start}-{layer_end} onto {device} ({dtype})..."
    )

    model = AutoModelForCausalLM.from_pretrained(
        source,
        config=config,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        device_map="cpu",
        **token_kwargs,
        **local_kwargs,
    )

    all_layers = _get_layers(model)

    assert len(all_layers) >= layer_end, (
        f"Model returned {len(all_layers)} layers but layer_end={layer_end}"
    )

    our_layers = nn.ModuleList(
        [all_layers[i] for i in range(layer_start, layer_end)]
    )

    assert len(our_layers) == layer_end - layer_start, (
        f"Expected {layer_end - layer_start} layers, got {len(our_layers)}"
    )

    del model
    torch.cuda.empty_cache()

    our_layers = our_layers.to(device=device, dtype=dtype)
    our_layers.eval()

    logger.info(f"Layers {layer_start}-{layer_end} loaded on {device}")
    return our_layers


def _get_layers(model: nn.Module) -> nn.ModuleList:
    """Extract transformer layer list — handles multiple architectures."""
    # Llama, Mistral, Gemma, Qwen
    if hasattr(model, "model") and hasattr(model.model, "layers"):
        return model.model.layers
    # Falcon, GPT-2, BLOOM
    if hasattr(model, "transformer") and hasattr(model.transformer, "h"):
        return model.transformer.h
    # OPT
    if hasattr(model, "model") and hasattr(model.model, "decoder") \
            and hasattr(model.model.decoder, "layers"):
        return model.model.decoder.layers
    raise ValueError(
        f"Cannot find transformer layers in {type(model).__name__}. "
        f"Add support for this architecture in block_loader._get_layers()"
    )


def _get_num_layers(config) -> int:
    """Get total layer count from model config."""
    if hasattr(config, "num_hidden_layers"):
        return config.num_hidden_layers
    if hasattr(config, "n_layer"):
        return config.n_layer
    if hasattr(config, "num_layers"):
        return config.num_layers
    raise ValueError(
        f"Cannot determine layer count from config type {type(config).__name__}"
    )
