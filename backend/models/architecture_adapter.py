from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch
import torch.nn as nn


@dataclass
class PreparedInputs:
    hidden_states: torch.Tensor
    attention_mask: Optional[torch.Tensor] = None
    position_ids: Optional[torch.Tensor] = None


class ArchitectureAdapter:
    """Minimal adapter interface for model-specific local preprocessing."""

    model_type: str = ""

    def prepare_inputs(
        self,
        model: nn.Module,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        raise NotImplementedError


class OPTAdapter(ArchitectureAdapter):
    model_type = "opt"

    def prepare_inputs(
        self,
        model: nn.Module,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        decoder = getattr(getattr(model, "model", None), "decoder", None)
        if decoder is None or not hasattr(decoder, "embed_tokens"):
            raise ValueError("OPT model is missing decoder.embed_tokens")

        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)

        inputs_embeds = decoder.embed_tokens(input_ids)
        if not hasattr(decoder, "embed_positions"):
            raise ValueError("OPT model is missing decoder.embed_positions")

        pos_embeds = decoder.embed_positions(
            attention_mask,
            0,
            position_ids=position_ids,
        )
        project_in = getattr(decoder, "project_in", None)
        if project_in is not None:
            inputs_embeds = project_in(inputs_embeds)

        return inputs_embeds + pos_embeds.to(inputs_embeds.device)


class LlamaAdapter(ArchitectureAdapter):
    model_type = "llama"

    def prepare_inputs(
        self,
        model: nn.Module,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        if not hasattr(model, "model") or not hasattr(model.model, "embed_tokens"):
            raise ValueError("Llama model is missing embed_tokens")
        return model.model.embed_tokens(input_ids)


def get_architecture_adapter(model_name: str) -> ArchitectureAdapter:
    normalized = model_name.lower()
    if "opt" in normalized:
        return OPTAdapter()
    if "llama" in normalized or "mistral" in normalized:
        return LlamaAdapter()
    raise ValueError(
        "Unsupported model architecture for distributed preprocessing: "
        f"{model_name}. Add an ArchitectureAdapter before enabling this model."
    )
