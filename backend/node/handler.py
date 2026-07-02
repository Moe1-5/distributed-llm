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
        self._rotary_embedding: Optional[nn.Module] = None
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
        position_ids = self._prepare_position_ids(position_ids, hidden_states)
        attention_mask = self._prepare_decoder_attention_mask(
            attention_mask,
            hidden_states,
        )
        position_embeddings = self._prepare_position_embeddings(
            hidden_states,
            position_ids,
        )

        with self._lock:
            with torch.no_grad():
                for layer in self.layers:
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

        return hidden_states.cpu()

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
    ) -> torch.Tensor:
        batch_size, seq_len, _ = hidden_states.shape
        device = hidden_states.device
        dtype = hidden_states.dtype

        if attention_mask is None:
            token_mask = torch.ones(
                (batch_size, seq_len),
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
            if attention_mask.shape != (batch_size, seq_len):
                raise ValueError(
                    f"attention_mask must be [batch, seq_len] = {(batch_size, seq_len)}, "
                    f"got {tuple(attention_mask.shape)}"
                )
            token_mask = attention_mask.to(dtype=torch.bool)

        causal_mask = torch.ones(
            (seq_len, seq_len),
            device=device,
            dtype=torch.bool,
        ).tril()
        allowed = causal_mask.unsqueeze(0).unsqueeze(0)
        allowed = allowed & token_mask[:, None, None, :]

        decoder_mask = torch.zeros(
            (batch_size, 1, seq_len, seq_len),
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
            assert self.layers is not None and len(self.layers) > 0
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
        }
