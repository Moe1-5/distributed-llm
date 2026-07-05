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
from constants import SUPPORTED_MODELS, DEFAULT_GEN_CONFIG
from client.sequential import RemoteSequential
from models.architecture_adapter import get_architecture_adapter

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
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if sequential is None:
            raise ValueError("sequential must not be None")

        self.model_name = model_name
        self.sequential = sequential
        self.device     = device
        self.dtype      = dtype
        self.hf_token   = hf_token

        self.tokenizer:    Optional[object]        = None
        self.embed_tokens: Optional[nn.Embedding]  = None
        self.position_embeddings: Optional[nn.Embedding] = None
        self.norm:         Optional[nn.Module]     = None
        self.lm_head:      Optional[nn.Linear]     = None
        self.architecture_adapter = None
        self._loaded_model: Optional[nn.Module] = None
        self._loaded = False
        self._stop_requested = False


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
        logger.info(
            f"Loading local components for {self.model_name} | "
            f"hf_token={'set' if self.hf_token else 'not set'}"
        )

        token_kwargs = {"token": self.hf_token} if self.hf_token else {}

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, **token_kwargs)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if self.tokenizer.eos_token_id is None:
            raise RuntimeError("Tokenizer has no EOS token")

        model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=self.dtype,
            low_cpu_mem_usage=True,
            device_map="cpu",
            **token_kwargs,
        )

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

        del model
        torch.cuda.empty_cache()

        self._loaded = True
        logger.info("Local components loaded.")

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

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate_stream(
        self,
        prompt:         str,
        max_new_tokens: Optional[int]   = None,
        temperature:    Optional[float] = None,
        top_p:          Optional[float] = None,
    ) -> AsyncGenerator[dict, None]:
        if not self._loaded:
            raise RuntimeError("Generator not loaded; call load() first")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        self.clear_stop()
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
        top_k          = cfg["top_k"]
        rep_penalty    = cfg["repetition_penalty"]


        try:
            input_ids     = self.tokenizer.encode(prompt, return_tensors="pt").to(self.device)
            generated_ids = input_ids.clone()
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

                hidden_states, trace = self.sequential.forward(
                    hidden_states=hidden_states,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                )

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
                    generated_ids=generated_ids,
                )

                if next_token_id.shape != (1, 1):
                    raise RuntimeError(f"Expected shape (1,1), got {next_token_id.shape}")
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
        self._stop_requested = True

    def clear_stop(self) -> None:
        self._stop_requested = False
