import asyncio
import sys
import time
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
from node.node import Node
from node.rpc_server import RPCServer


class DummyDHT:
    pass


class DummyDHTResult:
    def __init__(self, value):
        self.value = value


class MappingDHT:
    def __init__(self, values: dict[str, object]):
        self.values = values

    def get(self, key: str, latest: bool = True) -> DummyDHTResult | None:
        value = self.values.get(key)
        return DummyDHTResult(value) if value is not None else None


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

    def test_discover_nodes_skips_non_integer_layer_metadata(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["bad-peer", "good-peer"],
                "test-prefix.node_info.bad-peer": {
                    "peer_id": "bad-peer",
                    "model_name": "facebook/opt-125m",
                    "layer_start": "zero",
                    "layer_end": 4,
                    "rpc_uid": "test-prefix.0.0",
                },
                "test-prefix.node_info.good-peer": self.make_node(0, 8, "good-peer"),
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        nodes = sequential._discover_nodes()

        self.assertEqual([node["peer_id"] for node in nodes], ["good-peer"])

    def test_wrong_model_node_metadata_is_rejected(self) -> None:
        sequential = RemoteSequential(
            DummyDHT(),
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )
        nodes = [
            {
                **self.make_node(0, 8),
                "model_name": "meta-llama/Llama-3.2-1B",
            }
        ]

        with self.assertRaisesRegex(ValueError, "model_name"):
            sequential.validate_route(nodes)

    def test_negative_layer_ranges_do_not_count_as_valid_coverage(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        coverage = sequential._check_coverage([self.make_node(-2, 8)])

        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["covered"], [])
        self.assertEqual(coverage["invalid"], ["peer:-2-8"])

    def test_missing_rpc_uid_is_explicit_runtime_error(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        node = self.make_node(0, 8)
        node["rpc_uid"] = ""

        with self.assertRaisesRegex(ValueError, "rpc_uid"):
            sequential.validate_route([node])

    def test_rpc_forward_missing_expert_uses_runtime_error(self) -> None:
        import client.sequential as sequential_module

        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        original_get_experts = sequential_module.get_experts
        sequential_module.get_experts = lambda dht, uids: []
        try:
            with self.assertRaisesRegex(RuntimeError, "not found"):
                sequential._rpc_forward(
                    rpc_uid="test-prefix.0.0",
                    peer_id="peer",
                    hidden_states=torch.zeros(1, 1, 8),
                )
        finally:
            sequential_module.get_experts = original_get_experts

    def test_rpc_uid_includes_layer_slice_for_uniqueness(self) -> None:
        uid_a = RPCServer.build_rpc_uid("test-prefix", layer_start=0, layer_end=4)
        uid_b = RPCServer.build_rpc_uid("test-prefix", layer_start=4, layer_end=8)

        self.assertNotEqual(uid_a, uid_b)
        self.assertEqual(uid_a, "test-prefix.0.4")
        self.assertEqual(uid_b, "test-prefix.4.8")

    def test_rpc_stop_is_bounded_when_hivemind_shutdown_hangs(self) -> None:
        class BlockingServer:
            def shutdown(self) -> None:
                time.sleep(1.0)

        rpc = RPCServer.__new__(RPCServer)
        rpc._server = BlockingServer()
        rpc._running = True
        rpc._lock = __import__("threading").Lock()

        started = time.perf_counter()
        rpc.stop(timeout=0.01)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertFalse(rpc.is_running())
        self.assertIsNone(rpc._server)

    def test_node_stop_is_bounded_when_dht_shutdown_hangs(self) -> None:
        class FakeRPC:
            def __init__(self) -> None:
                self.timeout = None

            def stop(self, timeout: float = 5.0) -> None:
                self.timeout = timeout

            def is_running(self) -> bool:
                return False

        class FakeHandler:
            def __init__(self) -> None:
                self.unloaded = False

            def unload(self) -> None:
                self.unloaded = True

            def is_loaded(self) -> bool:
                return False

        class BlockingDHT:
            peer_id = "peer"

            def shutdown(self) -> None:
                time.sleep(1.0)

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        fake_rpc = FakeRPC()
        fake_handler = FakeHandler()
        node.rpc = fake_rpc
        node.handler = fake_handler
        node.dht = BlockingDHT()
        node._running = True

        started = time.perf_counter()
        node.stop(timeout=0.01)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual(fake_rpc.timeout, 0.01)
        self.assertTrue(fake_handler.unloaded)
        self.assertIsNone(node.rpc)
        self.assertIsNone(node.handler)
        self.assertIsNone(node.dht)
        self.assertFalse(node.is_running())

    def test_nodes_endpoint_uses_active_node_prefix(self) -> None:
        from api import server as api_server

        seen_prefixes: list[str] = []

        class DummyNode:
            dht = object()
            dht_prefix = "custom-prefix"

        class CaptureSequential:
            def __init__(
                self,
                dht,
                dht_prefix: str,
                num_layers: int,
                model_name: str | None = None,
            ):
                seen_prefixes.append(dht_prefix)

            def get_network_status(self) -> dict:
                return {"nodes": []}

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        original_sequential = api_server.RemoteSequential
        api_server.node = DummyNode()
        api_server.client_dht = None
        api_server.RemoteSequential = CaptureSequential
        try:
            asyncio.run(api_server.get_nodes())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht
            api_server.RemoteSequential = original_sequential

        self.assertEqual(seen_prefixes, ["custom-prefix"])

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

    def test_generate_stream_honors_stop_request_after_route_step(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class StopSequential:
            def __init__(self) -> None:
                self.generator: DistributedGenerator | None = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                assert self.generator is not None
                self.generator.request_stop()
                return hidden_states, ["peer… (layers 0→1)"]

        sequential = StopSequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        sequential.generator = generator
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=4)]

        result = asyncio.run(run_generation())

        self.assertEqual(result, [{"done": True, "node_trace": ["peer… (layers 0→1)"]}])

    def test_generator_status_reports_not_loaded(self) -> None:
        from api import server as api_server

        original_generator = api_server.generator
        api_server.generator = None
        try:
            result = asyncio.run(api_server.get_generator_status())
        finally:
            api_server.generator = original_generator

        self.assertFalse(result["ready"])
        self.assertEqual(result["reasons"], ["Generator not loaded."])

    def test_generator_status_reports_route_validation_error(self) -> None:
        from api import server as api_server

        class DummySequential:
            def validate_route(self) -> list[dict]:
                raise RuntimeError("missing layers")

        class DummyGenerator:
            model_name = "facebook/opt-125m"
            sequential = DummySequential()

            def is_loaded(self) -> bool:
                return True

        original_generator = api_server.generator
        api_server.generator = DummyGenerator()
        try:
            result = asyncio.run(api_server.get_generator_status())
        finally:
            api_server.generator = original_generator

        self.assertFalse(result["ready"])
        self.assertFalse(result["route_ready"])
        self.assertEqual(result["reasons"], ["missing layers"])

    def test_stop_generator_requests_cancellation(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.stop_requested = False

            def is_loaded(self) -> bool:
                return True

            def request_stop(self) -> None:
                self.stop_requested = True

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            result = asyncio.run(api_server.stop_generator())
        finally:
            api_server.generator = original_generator

        self.assertEqual(result["status"], "stop_requested")
        self.assertTrue(dummy.stop_requested)

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
