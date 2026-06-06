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

from typing import AsyncGenerator, Optional

import torch
import torch.nn as nn
from hivemind.utils.logging import get_logger
from transformers import AutoTokenizer, AutoModelForCausalLM

from client.sequential import RemoteSequential

logger = get_logger(__name__)


class DistributedGenerator:
    def __init__(
        self,
        model_name: str,
        sequential: RemoteSequential,
        device:     str           = "cpu",
        dtype:      torch.dtype   = torch.float32,
        hf_token:   Optional[str] = None,
    ):
        assert model_name.strip(),   "model_name must not be empty"
        assert sequential is not None, "sequential must not be None"

        self.model_name = model_name
        self.sequential = sequential
        self.device     = device
        self.dtype      = dtype
        self.hf_token   = hf_token

        self.tokenizer:    Optional[object]        = None
        self.embed_tokens: Optional[nn.Embedding]  = None
        self.norm:         Optional[nn.Module]     = None
        self.lm_head:      Optional[nn.Linear]     = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self) -> None:
        logger.info(
            f"Loading local components for {self.model_name} | "
            f"hf_token={'set' if self.hf_token else 'not set'}"
        )

        token_kwargs = {"token": self.hf_token} if self.hf_token else {}

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, **token_kwargs)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=self.dtype,
            low_cpu_mem_usage=True,
            device_map="cpu",
            **token_kwargs,
        )

        self.embed_tokens = self._extract_embed_tokens(model)
        self.norm         = self._extract_norm(model)
        self.lm_head      = self._extract_lm_head(model)

        for component in (self.embed_tokens, self.norm, self.lm_head):
            component.to(self.device)
            component.eval()
            component.requires_grad_(False)

        del model
        torch.cuda.empty_cache()

        self._loaded = True
        logger.info("Local components loaded.")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate_stream(
        self,
        prompt:         str,
        max_new_tokens: int   = 200,
        temperature:    float = 0.7,
        top_p:          float = 0.9,
    ) -> AsyncGenerator[dict, None]:
        assert self._loaded,          "Generator not loaded — call load() first"
        assert prompt.strip(),        "prompt must not be empty"
        assert 1 <= max_new_tokens <= 2048
        assert 0.0 < temperature <= 2.0
        assert 0.0 < top_p <= 1.0

        try:
            input_ids     = self.tokenizer.encode(prompt, return_tensors="pt").to(self.device)
            generated_ids = input_ids.clone()
            node_trace:   list[str] = []

            for step in range(max_new_tokens):
                with torch.no_grad():
                    hidden_states = self.embed_tokens(generated_ids)

                position_ids = torch.arange(
                    generated_ids.shape[1], device=self.device
                ).unsqueeze(0)

                hidden_states, trace = self.sequential.forward(
                    hidden_states=hidden_states,
                    position_ids=position_ids,
                )

                if step == 0:
                    node_trace = trace

                hidden_states = hidden_states.to(self.device)

                hidden_states = hidden_states.to(self.dtype)

                with torch.no_grad():
                    hidden_states = self.norm(hidden_states)
                    logits        = self.lm_head(hidden_states)

                next_token_logits = logits[:, -1, :]
                next_token_id     = self._sample(
                    next_token_logits,
                    temperature=temperature,
                    top_p=top_p,
                )

                assert next_token_id.shape == (1, 1), (
                    f"Expected shape (1,1), got {next_token_id.shape}"
                )

                token_text = self.tokenizer.decode(
                    next_token_id[0],
                    skip_special_tokens=True,
                )
                if token_text:
                    yield {"token": token_text}

                generated_ids = torch.cat([generated_ids, next_token_id], dim=1)

                if next_token_id.item() == self.tokenizer.eos_token_id:
                    logger.debug(f"EOS at step {step}")
                    break

            yield {"done": True, "node_trace": node_trace}

        except Exception as e:
            logger.error(f"Generation error: {e}", exc_info=True)
            yield {"error": str(e)}

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _sample(
        self,
        logits:      torch.Tensor,
        temperature: float = 0.7,
        top_p:       float = 0.9,
    ) -> torch.Tensor:
        assert logits.dim() == 2, f"Expected [1, vocab], got {logits.shape}"

        if temperature > 0:
            logits = logits / temperature

        probs = torch.softmax(logits, dim=-1)

        if top_p < 1.0:
            sorted_probs, sorted_indices = torch.sort(probs, descending=True)
            cumulative = torch.cumsum(sorted_probs, dim=-1)
            sorted_probs[(cumulative - sorted_probs) > top_p] = 0.0
            total = sorted_probs.sum(dim=-1, keepdim=True)
            assert total > 0, "All probabilities zeroed in top-p sampling"
            sorted_probs  = sorted_probs / total
            sampled       = torch.multinomial(sorted_probs, num_samples=1)
            next_token    = sorted_indices.gather(-1, sampled)
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
