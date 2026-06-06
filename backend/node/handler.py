"""
handler.py
Loads transformer layers and handles forward pass requests.
Thread-safe — multiple requests queue via a lock.
"""

import threading
from typing import Optional

import torch
import torch.nn as nn
from hivemind.utils.logging import get_logger

from node.block_loader import load_layers

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
    ):
        assert model_name.strip(),       "model_name must not be empty"
        assert layer_end > layer_start,  f"layer_end ({layer_end}) must be > layer_start ({layer_start})"
        assert device in ("cuda", "cpu"), f"device must be 'cuda' or 'cpu'"

        self.model_name  = model_name
        self.layer_start = layer_start
        self.layer_end   = layer_end
        self.device      = device
        self.dtype       = dtype
        self.hf_token    = hf_token

        self.layers: Optional[nn.ModuleList] = None
        self._lock   = threading.Lock()
        self._loaded = False

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
        )
        assert self.layers is not None,         "load_layers returned None"
        assert len(self.layers) > 0,            "load_layers returned empty ModuleList"
        assert len(self.layers) == self.layer_end - self.layer_start, (
            f"Expected {self.layer_end - self.layer_start} layers, got {len(self.layers)}"
        )
        self._loaded = True
        logger.info(f"Loaded {len(self.layers)} layers on {self.device}")

    def unload(self) -> None:
        if self.layers is not None:
            del self.layers
            self.layers = None
            if self.device == "cuda":
                torch.cuda.empty_cache()
        self._loaded = False
        logger.info("Layers unloaded.")

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def forward(
        self,
        hidden_states:  torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        if not self._loaded or self.layers is None:
            raise RuntimeError("Layers not loaded. Call load() first.")

        assert hidden_states.dim() == 3, (
            f"hidden_states must be [batch, seq_len, hidden_size], got {hidden_states.shape}"
        )

        hidden_states = hidden_states.to(self.device, dtype=self.dtype)

        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)

        if position_ids is not None:
            position_ids = position_ids.to(self.device)
        else:
            seq_len      = hidden_states.shape[1]
            position_ids = torch.arange(seq_len, device=self.device).unsqueeze(0)

# THIS WAS CHANGED TOO -- FROM INFERENCE_MODE TO NO_GRAD 
        
        with self._lock:
            with torch.no_grad():
                for layer in self.layers:
                    out           = layer(
                        hidden_states,
                        attention_mask=attention_mask,
                        position_ids=position_ids,
                    )
                    hidden_states = out[0] if isinstance(out, tuple) else out

        return hidden_states.cpu()

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
        }
