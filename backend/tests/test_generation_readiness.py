import asyncio
import sys
import unittest
from pathlib import Path

import torch
import torch.nn as nn

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.generation import DistributedGenerator
from client.sequential import RemoteSequential
from models.architecture_adapter import get_architecture_adapter
from node.handler import InferenceHandler


class DummyDHT:
    pass


class RemoteSequentialRouteTests(unittest.TestCase):
    def make_node(self, layer_start: int, layer_end: int, peer_id: str = "peer") -> dict:
        return {
            "peer_id": peer_id,
            "layer_start": layer_start,
            "layer_end": layer_end,
            "model_name": "facebook/opt-125m",
            "rpc_uid": "uid-123",
        }

    def test_plan_route_returns_contiguous_non_overlapping_spans(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        nodes = [self.make_node(0, 4), self.make_node(4, 8)]
        plan = sequential._plan_route(nodes)

        self.assertEqual([node["layer_start"] for node in plan], [0, 4])
        self.assertEqual([node["layer_end"] for node in plan], [4, 8])

    def test_plan_route_rejects_overlapping_spans(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        with self.assertRaisesRegex(ValueError, "not contiguous"):
            sequential._plan_route([self.make_node(0, 5), self.make_node(4, 8)])

    def test_validate_route_raises_for_incomplete_coverage(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        sequential._discover_nodes = lambda: [self.make_node(0, 4), self.make_node(6, 8)]

        with self.assertRaisesRegex(RuntimeError, "Incomplete layer coverage"):
            sequential.validate_route()

    def test_prepare_hidden_states_adds_position_embeddings(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)

        input_ids = torch.tensor([[1, 2, 3]])
        position_ids = torch.tensor([[0, 1, 2]])

        attention_mask = torch.ones_like(input_ids, dtype=torch.bool)

        hidden = generator._prepare_hidden_states(input_ids, attention_mask, position_ids)

        expected = generator.embed_tokens(input_ids) + generator.position_embeddings(position_ids)
        self.assertTrue(torch.allclose(hidden, expected))

    def test_opt_adapter_matches_huggingface_embedding_reference(self) -> None:
        from transformers import OPTConfig, OPTForCausalLM

        adapter = get_architecture_adapter("facebook/opt-125m")
        model = OPTForCausalLM(
            OPTConfig(
                vocab_size=20,
                hidden_size=8,
                word_embed_proj_dim=8,
                ffn_dim=16,
                num_hidden_layers=1,
                num_attention_heads=2,
                max_position_embeddings=16,
            )
        )

        input_ids = torch.tensor([[1, 2, 3]])
        attention_mask = torch.ones_like(input_ids)
        position_ids = torch.tensor([[3, 4, 5]])

        hidden = adapter.prepare_inputs(
            model,
            input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )

        decoder = model.model.decoder
        inputs_embeds = decoder.embed_tokens(input_ids)
        pos_embeds = decoder.embed_positions(
            attention_mask,
            0,
            position_ids=position_ids,
        )
        expected = inputs_embeds + pos_embeds.to(inputs_embeds.device)
        self.assertTrue(torch.allclose(hidden, expected))

    def test_adapter_selection_rejects_unsupported_architecture(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported model architecture"):
            get_architecture_adapter("unknown/future-model")

    def test_generator_readiness_rejects_missing_components(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())

        with self.assertRaisesRegex(RuntimeError, "missing local component"):
            generator._validate_loaded_components()

    def test_generator_readiness_rejects_hidden_size_mismatch(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())
        generator.tokenizer = object()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator.architecture_adapter = get_architecture_adapter("facebook/opt-125m")
        generator._loaded_model = nn.Module()

        with self.assertRaisesRegex(RuntimeError, "hidden size mismatch"):
            generator._validate_loaded_components()

    def test_generate_stream_initializes_position_ids_for_hidden_state_preparation(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class DummySequential:
            def __init__(self) -> None:
                self.received_position_ids = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                self.received_position_ids = position_ids
                return hidden_states, []

        tokenizer = DummyTokenizer()
        sequential = DummySequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        generator._loaded = True
        generator.tokenizer = tokenizer
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._sample = lambda logits, **kwargs: torch.tensor([[1]])

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=1)]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertIsNotNone(sequential.received_position_ids)
        self.assertEqual(tuple(sequential.received_position_ids.shape), (1, 2))

    def test_generate_stream_updates_position_ids_across_steps(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class DummySequential:
            def __init__(self) -> None:
                self.received_position_ids: list[torch.Tensor] = []

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                self.received_position_ids.append(position_ids.clone())
                return hidden_states, []

        tokenizer = DummyTokenizer()
        sequential = DummySequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        generator._loaded = True
        generator.tokenizer = tokenizer
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._sample = lambda logits, **kwargs: torch.tensor([[1]])

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=2)]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertEqual(len(sequential.received_position_ids), 2)
        self.assertEqual(tuple(sequential.received_position_ids[0].shape), (1, 2))
        self.assertEqual(tuple(sequential.received_position_ids[1].shape), (1, 3))

    def test_handler_expands_token_mask_to_causal_decoder_mask(self) -> None:
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        hidden_states = torch.zeros(1, 4, 8)
        token_mask = torch.tensor([[1, 1, 1, 0]], dtype=torch.bool)

        decoder_mask = handler._prepare_decoder_attention_mask(
            token_mask,
            hidden_states,
        )

        self.assertEqual(tuple(decoder_mask.shape), (1, 1, 4, 4))
        self.assertEqual(decoder_mask.dtype, torch.float32)
        self.assertEqual(decoder_mask[0, 0, 2, 0].item(), 0.0)
        self.assertEqual(decoder_mask[0, 0, 2, 2].item(), 0.0)
        self.assertEqual(decoder_mask[0, 0, 2, 3].item(), torch.finfo(torch.float32).min)
        self.assertEqual(decoder_mask[0, 0, 0, 1].item(), torch.finfo(torch.float32).min)

    def test_handler_forward_passes_layer_ready_attention_mask(self) -> None:
        class CaptureLayer(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.received_attention_mask = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> torch.Tensor:
                self.received_attention_mask = attention_mask
                return hidden_states + 1

        layer = CaptureLayer()
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([layer])
        handler._loaded = True

        output = handler.forward(
            hidden_states=torch.zeros(1, 3, 8),
            attention_mask=torch.ones(1, 3, dtype=torch.bool),
            position_ids=torch.tensor([[0, 1, 2]]),
        )

        self.assertTrue(torch.allclose(output, torch.ones(1, 3, 8)))
        self.assertIsNotNone(layer.received_attention_mask)
        self.assertEqual(tuple(layer.received_attention_mask.shape), (1, 1, 3, 3))

    def test_handler_prepares_llama_position_embeddings(self) -> None:
        from transformers import LlamaConfig

        class LlamaLikeLayer(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.self_attn = nn.Module()
                self.self_attn.config = LlamaConfig(
                    hidden_size=8,
                    intermediate_size=16,
                    num_attention_heads=2,
                    num_key_value_heads=2,
                    num_hidden_layers=1,
                )

        handler = InferenceHandler(
            model_name="meta-llama/Llama-3.2-1B",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([LlamaLikeLayer()])
        hidden_states = torch.zeros(1, 3, 8)
        position_ids = torch.tensor([[0, 1, 2]])

        position_embeddings = handler._prepare_position_embeddings(
            hidden_states,
            position_ids,
        )

        self.assertIsNotNone(position_embeddings)
        cos, sin = position_embeddings
        self.assertEqual(tuple(cos.shape), (1, 3, 4))
        self.assertEqual(tuple(sin.shape), (1, 3, 4))


if __name__ == "__main__":
    unittest.main()
