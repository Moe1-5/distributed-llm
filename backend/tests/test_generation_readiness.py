import asyncio
import json
import sys
import time
import tempfile
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

    def test_discovered_node_running_defaults_to_loaded_rpc_state(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["peer"],
                "test-prefix.node_info.peer": {
                    **self.make_node(0, 8, "peer"),
                    "layers_loaded": True,
                    "rpc_running": True,
                },
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        nodes = sequential._discover_nodes()

        self.assertEqual(len(nodes), 1)
        self.assertTrue(nodes[0]["running"])
        self.assertEqual(nodes[0]["maddrs"], [])

    def test_validate_route_ignores_offline_loaded_nodes(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        offline_node = {
            **self.make_node(0, 8, "offline-peer"),
            "layers_loaded": True,
            "rpc_running": False,
            "running": False,
        }
        sequential._discover_nodes = lambda: [offline_node]

        with self.assertRaisesRegex(RuntimeError, "No serving nodes"):
            sequential.validate_route()

    def test_network_status_keeps_offline_node_but_excludes_it_from_coverage(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["offline-peer"],
                "test-prefix.node_info.offline-peer": {
                    **self.make_node(0, 8, "offline-peer"),
                    "layers_loaded": True,
                    "rpc_running": False,
                    "running": False,
                },
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        status = sequential.get_network_status()

        self.assertEqual(len(status["nodes"]), 1)
        self.assertEqual(status["covered_layers"], 0)
        self.assertEqual(status["missing_layers"], list(range(8)))

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

    def test_node_turn_off_stops_rpc_but_keeps_loaded_layers(self) -> None:
        class FakeRPC:
            def __init__(self) -> None:
                self.timeout = None
                self.running = True

            def stop(self, timeout: float = 5.0) -> None:
                self.timeout = timeout
                self.running = False

            def is_running(self) -> bool:
                return self.running

            def get_uid(self) -> str:
                return "test-prefix.0.1"

        class FakeHandler:
            def __init__(self) -> None:
                self.unloaded = False

            def unload(self) -> None:
                self.unloaded = True

            def is_loaded(self) -> bool:
                return not self.unloaded

            def get_accounting_snapshot(self) -> dict:
                return {}

        class FakeDHT:
            peer_id = "peer"

            def __init__(self) -> None:
                self.values: dict[str, object] = {}

            def store(self, key: str, value: object, expiration_time: float) -> None:
                self.values[key] = value

            def get(self, key: str, latest: bool = True) -> DummyDHTResult | None:
                value = self.values.get(key)
                return DummyDHTResult(value) if value is not None else None

            def get_visible_maddrs(self) -> list[str]:
                return ["/ip4/127.0.0.1/tcp/1234"]

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        fake_rpc = FakeRPC()
        fake_handler = FakeHandler()
        fake_dht = FakeDHT()
        node.rpc = fake_rpc
        node.handler = fake_handler
        node.dht = fake_dht
        node._running = True
        node._ensure_announce_thread = lambda: None

        node.turn_off(timeout=0.01)

        self.assertEqual(fake_rpc.timeout, 0.01)
        self.assertFalse(fake_rpc.is_running())
        self.assertFalse(fake_handler.unloaded)
        self.assertIs(node.handler, fake_handler)
        self.assertIsNone(node.rpc)
        self.assertIsNone(node.dht)
        self.assertFalse(node.is_running())
        info = fake_dht.values["test-prefix.node_info.peer"]
        self.assertTrue(info["layers_loaded"])
        self.assertFalse(info["rpc_running"])
        self.assertFalse(info["running"])
        status = node.get_info()
        self.assertEqual(status["peer_id"], "peer")
        self.assertEqual(status["maddrs"], ["/ip4/127.0.0.1/tcp/1234"])
        self.assertTrue(status["layers_loaded"])
        self.assertFalse(status["rpc_running"])

    def test_get_visible_maddrs_falls_back_when_dht_handle_is_closed(self) -> None:
        class ClosedDHT:
            peer_id = "peer"

            def get_visible_maddrs(self) -> list[str]:
                raise OSError("handle is closed")

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        node.dht = ClosedDHT()
        node._last_maddrs = ["/ip4/127.0.0.1/tcp/1234"]

        self.assertEqual(node.get_visible_maddrs(), ["/ip4/127.0.0.1/tcp/1234"])

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

    def test_generate_stream_passes_exact_generation_controls_to_sampling(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, []

        received_kwargs: dict[str, object] = {}
        generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential=IdentitySequential(),
            device="cpu",
        )
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)

        def capture_sample(logits: torch.Tensor, **kwargs) -> torch.Tensor:
            received_kwargs.update(kwargs)
            return torch.tensor([[1]])

        generator._sample = capture_sample

        async def run_generation() -> list[dict]:
            return [
                item
                async for item in generator.generate_stream(
                    "hello",
                    max_new_tokens=1,
                    temperature=0.25,
                    top_p=1.0,
                    top_k=0,
                    repetition_penalty=1.0,
                    do_sample=False,
                )
            ]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertEqual(received_kwargs["temperature"], 0.25)
        self.assertEqual(received_kwargs["top_p"], 1.0)
        self.assertEqual(received_kwargs["top_k"], 0)
        self.assertEqual(received_kwargs["repetition_penalty"], 1.0)
        self.assertFalse(received_kwargs["do_sample"])

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

    def test_sample_uses_argmax_when_do_sample_is_false(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())

        result = generator._sample(
            torch.tensor([[0.1, 2.0, 1.5]]),
            temperature=0.1,
            top_p=0.1,
            top_k=1,
            repetition_penalty=1.0,
            do_sample=False,
        )

        self.assertEqual(result.tolist(), [[1]])

    def test_compare_next_token_logits_matches_direct_reference(self) -> None:
        class DummyTokenizer:
            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                token_id = int(token_ids[0])
                return {0: "<eos>", 1: "a", 2: "b", 3: "c"}.get(token_id, "?")

        class TinyCausalLM(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.embed = nn.Embedding(4, 3)
                self.lm_head = nn.Linear(3, 4, bias=False)

            def forward(
                self,
                input_ids: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
            ):
                logits = self.lm_head(self.embed(input_ids))
                return type("TinyOutput", (), {"logits": logits})

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        model = TinyCausalLM()
        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = model.embed
        generator.norm = nn.Identity()
        generator.lm_head = model.lm_head
        generator._loaded_model = model
        generator.architecture_adapter = None

        result = generator.compare_next_token_logits("hello")

        self.assertTrue(result["allclose"])
        self.assertTrue(result["argmax_match"])
        self.assertEqual(result["max_abs_diff"], 0.0)
        self.assertEqual(result["mean_abs_diff"], 0.0)
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_compare_next_token_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received_prompt = None
                self.received_atol = None
                self.received_rtol = None

            def is_loaded(self) -> bool:
                return True

            def compare_next_token_logits(
                self,
                prompt: str,
                atol: float,
                rtol: float,
            ) -> dict:
                self.received_prompt = prompt
                self.received_atol = atol
                self.received_rtol = rtol
                return {
                    "prompt": prompt,
                    "argmax_match": True,
                    "allclose": True,
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            request = api_server.NextTokenParityRequest(
                prompt="The capital of France is",
                atol=0.01,
                rtol=0.02,
            )
            result = asyncio.run(api_server.compare_generator_next_token(request))
        finally:
            api_server.generator = original_generator

        self.assertEqual(result["prompt"], "The capital of France is")
        self.assertTrue(result["allclose"])
        self.assertEqual(dummy.received_prompt, "The capital of France is")
        self.assertEqual(dummy.received_atol, 0.01)
        self.assertEqual(dummy.received_rtol, 0.02)

    def test_compare_generated_output_matches_direct_reference_greedy(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0
            pad_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                if isinstance(token_ids, torch.Tensor):
                    token_ids = token_ids.tolist()
                return "".join(
                    {0: "", 1: "a", 2: "b", 3: "c"}.get(int(token_id), "?")
                    for token_id in token_ids
                )

        class TinyGenerateModel(nn.Module):
            def to(self, device):
                return self

            def eval(self):
                return self

            def generate(self, **kwargs):
                return torch.tensor([[1, 2, 3]])

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._loaded_model = TinyGenerateModel()
        generator.architecture_adapter = None
        generator._sample = lambda logits, **kwargs: torch.tensor([[3]])

        result = asyncio.run(
            generator.compare_generated_output(
                "hello",
                max_new_tokens=1,
                repetition_penalty=1.0,
                do_sample=False,
            )
        )

        self.assertEqual(result["direct_response"], "c")
        self.assertEqual(result["distributed_response"], "c")
        self.assertTrue(result["exact_text_match"])
        self.assertFalse(result["generation_config"]["do_sample"])
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_compare_generated_output_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received: dict[str, object] = {}

            def is_loaded(self) -> bool:
                return True

            async def compare_generated_output(
                self,
                prompt: str,
                max_new_tokens: int | None,
                temperature: float | None,
                top_p: float | None,
                top_k: int | None,
                repetition_penalty: float | None,
                do_sample: bool | None,
            ) -> dict:
                self.received = {
                    "prompt": prompt,
                    "max_new_tokens": max_new_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "repetition_penalty": repetition_penalty,
                    "do_sample": do_sample,
                }
                return {
                    "prompt": prompt,
                    "direct_response": "c",
                    "distributed_response": "c",
                    "exact_text_match": True,
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            request = api_server.GeneratedParityRequest(
                prompt="hello",
                max_new_tokens=3,
                temperature=0.2,
                top_p=1.0,
                top_k=0,
                repetition_penalty=1.0,
                do_sample=False,
            )
            result = asyncio.run(api_server.compare_generator_output(request))
        finally:
            api_server.generator = original_generator

        self.assertTrue(result["exact_text_match"])
        self.assertEqual(dummy.received["prompt"], "hello")
        self.assertEqual(dummy.received["max_new_tokens"], 3)
        self.assertEqual(dummy.received["top_k"], 0)
        self.assertEqual(dummy.received["repetition_penalty"], 1.0)
        self.assertFalse(dummy.received["do_sample"])

    def test_trace_generation_records_token_steps(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                token_id = int(token_ids[0])
                return {0: "<eos>", 1: "a", 2: "b", 3: "c"}.get(token_id, "?")

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(4, 3)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(3, 4, bias=False)
        generator.architecture_adapter = None

        result = generator.trace_generation("hello", max_new_tokens=1)

        self.assertEqual(result["prompt_token_ids"], [1, 2])
        self.assertTrue(result["generation_config"]["do_sample"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertIn("token_id", result["steps"][0])
        self.assertIn("decoded_output_so_far", result["steps"][0])
        self.assertIn("top_candidates", result["steps"][0])
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_trace_generation_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received_prompt = None
                self.received_max_new_tokens = None

            def is_loaded(self) -> bool:
                return True

            def trace_generation(
                self,
                prompt: str,
                max_new_tokens: int | None,
                temperature: float | None,
                top_p: float | None,
                top_k: int | None,
                repetition_penalty: float | None,
                do_sample: bool | None,
            ) -> dict:
                self.received_prompt = prompt
                self.received_max_new_tokens = max_new_tokens
                return {
                    "prompt": prompt,
                    "steps": [],
                    "generation_config": {
                        "top_k": top_k,
                        "repetition_penalty": repetition_penalty,
                        "do_sample": do_sample,
                    },
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        original_trace_dir = api_server.TRACE_DIR
        api_server.generator = dummy
        with tempfile.TemporaryDirectory() as trace_dir:
            api_server.TRACE_DIR = Path(trace_dir)
            try:
                request = api_server.GenerationTraceRequest(
                    prompt="test",
                    max_new_tokens=4,
                    top_k=0,
                    repetition_penalty=1.0,
                    do_sample=False,
                )
                result = asyncio.run(api_server.trace_generator(request))
            finally:
                api_server.generator = original_generator
                api_server.TRACE_DIR = original_trace_dir

            self.assertEqual(result["prompt"], "test")
            self.assertEqual(dummy.received_prompt, "test")
            self.assertEqual(dummy.received_max_new_tokens, 4)
            self.assertEqual(result["generation_config"]["top_k"], 0)
            self.assertEqual(result["generation_config"]["repetition_penalty"], 1.0)
            self.assertFalse(result["generation_config"]["do_sample"])
            self.assertIn("trace_id", result)
            self.assertIn("trace_file", result)
            trace_document = json.loads(Path(result["trace_file"]).read_text(encoding="utf-8"))
            self.assertEqual(trace_document["trace_id"], result["trace_id"])
            self.assertEqual(trace_document["trace"]["prompt"], "test")

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

    def test_models_report_not_runnable_without_dht_connection(self) -> None:
        from api import server as api_server

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        api_server.node = None
        api_server.client_dht = None
        try:
            result = asyncio.run(api_server.get_models())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht

        opt = next(model for model in result["models"] if model["id"] == "facebook/opt-125m")
        self.assertFalse(opt["runnable"])
        self.assertFalse(opt["route_ready"])
        self.assertEqual(opt["covered_layers"], 0)
        self.assertEqual(opt["missing_layers"], list(range(12)))
        self.assertEqual(opt["route_reasons"], ["No DHT connection yet."])

    def test_models_report_runnable_for_complete_compatible_route(self) -> None:
        from api import server as api_server

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
            ) -> None:
                self.num_layers = num_layers
                self.model_name = model_name

            def get_network_status(self) -> dict:
                if self.model_name == "facebook/opt-125m":
                    return {
                        "nodes": [
                            {
                                "peer_id": "peer-123456",
                                "layer_start": 0,
                                "layer_end": 12,
                                "model_name": "facebook/opt-125m",
                                "rpc_uid": "uid",
                            }
                        ],
                        "covered_layers": 12,
                        "missing_layers": [],
                    }
                return {
                    "nodes": [],
                    "covered_layers": 0,
                    "missing_layers": list(range(self.num_layers)),
                }

            def validate_route(self, nodes: list[dict] | None = None) -> list[dict]:
                if self.model_name != "facebook/opt-125m":
                    raise RuntimeError("No compatible route")
                assert nodes is not None
                return nodes

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        original_sequential = api_server.RemoteSequential
        api_server.node = DummyNode()
        api_server.client_dht = None
        api_server.RemoteSequential = CaptureSequential
        try:
            result = asyncio.run(api_server.get_models())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht
            api_server.RemoteSequential = original_sequential

        opt = next(model for model in result["models"] if model["id"] == "facebook/opt-125m")
        self.assertTrue(opt["runnable"])
        self.assertTrue(opt["route_ready"])
        self.assertEqual(opt["covered_layers"], 12)
        self.assertEqual(opt["missing_layers"], [])
        self.assertEqual(opt["compatible_nodes"], 1)
        self.assertEqual(opt["route_trace"], ["peer-123… (layers 0→12)"])

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

    def test_turn_off_node_preserves_global_node(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self) -> None:
                self.turned_off = False

            def is_running(self) -> bool:
                return not self.turned_off

            def turn_off(self) -> None:
                self.turned_off = True

            def get_info(self) -> dict:
                return {"running": self.is_running(), "layers_loaded": True}

        dummy = DummyNode()
        original_node = api_server.node
        api_server.node = dummy
        try:
            result = asyncio.run(api_server.turn_off_node())
        finally:
            api_server.node = original_node

        self.assertEqual(result["status"], "turned_off")
        self.assertTrue(dummy.turned_off)
        self.assertIs(result["info"]["layers_loaded"], True)

    def test_start_node_allows_second_non_overlapping_local_slice(self) -> None:
        from api import server as api_server

        created: list[object] = []

        class DummyNode:
            def __init__(
                self,
                model_name: str,
                layer_start: int,
                layer_end: int,
                dht_prefix: str,
                initial_peers: list[str],
                device: str,
                hf_token: str | None,
            ) -> None:
                self.node_id = f"node-{len(created) + 1}"
                self.model_name = model_name
                self.layer_start = layer_start
                self.layer_end = layer_end
                self.dht_prefix = dht_prefix
                self.initial_peers = initial_peers
                self.device = device
                self.hf_token = hf_token
                self.dht = object()
                self.running = False
                created.append(self)

            def start(self) -> None:
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": self.node_id,
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "device": self.device,
                    "running": self.running,
                    "maddrs": [],
                    "layers_loaded": True,
                    "rpc_running": self.running,
                }

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        original_node_class = api_server.Node
        api_server.node = None
        api_server.local_nodes.clear()
        api_server.Node = DummyNode
        try:
            first = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=0,
                        layer_end=6,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
            second = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=6,
                        layer_end=12,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(first["status"], "started")
        self.assertEqual(second["status"], "started")
        self.assertEqual(first["info"]["node_id"], "node-1")
        self.assertEqual(second["info"]["node_id"], "node-2")

    def test_start_node_rejects_overlapping_or_different_model_local_slice(self) -> None:
        from api import server as api_server

        class DummyNode:
            node_id = "node-1"
            model_name = "facebook/opt-125m"
            layer_start = 0
            layer_end = 6
            dht_prefix = "test-prefix"
            device = "cpu"

            def is_running(self) -> bool:
                return True

            def get_info(self) -> dict:
                return {"node_id": self.node_id}

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        dummy = DummyNode()
        api_server.node = dummy
        api_server.local_nodes.clear()
        api_server.local_nodes[dummy.node_id] = dummy
        try:
            overlap = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=4,
                        layer_end=8,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
            different_model = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-1.3b",
                        layer_start=6,
                        layer_end=12,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(overlap["status"], "error")
        self.assertEqual(overlap["error"], "overlapping_layer_range")
        self.assertEqual(different_model["status"], "error")
        self.assertEqual(different_model["error"], "multi_model_local_nodes_not_supported")

    def test_turn_off_node_targets_one_local_node_by_id(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.model_name = "facebook/opt-125m"
                self.layer_start = 0
                self.layer_end = 1
                self.dht_prefix = "test-prefix"
                self.device = "cpu"
                self.turned_off = False

            def is_running(self) -> bool:
                return not self.turned_off

            def turn_off(self) -> None:
                self.turned_off = True

            def get_info(self) -> dict:
                return {"node_id": self.node_id, "running": self.is_running()}

        first = DummyNode("node-1")
        second = DummyNode("node-2")
        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        api_server.local_nodes.clear()
        api_server.local_nodes[first.node_id] = first
        api_server.local_nodes[second.node_id] = second
        api_server.node = first
        try:
            result = asyncio.run(api_server.turn_off_node(node_id="node-1"))
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "turned_off")
        self.assertTrue(first.turned_off)
        self.assertFalse(second.turned_off)

    def test_nodes_endpoint_returns_local_loaded_node_without_active_dht(self) -> None:
        from api import server as api_server

        class DummyNode:
            dht = None

            def get_info(self) -> dict:
                return {
                    "peer_id": "peer",
                    "model_name": "facebook/opt-125m",
                    "layer_start": 0,
                    "layer_end": 1,
                    "device": "cpu",
                    "running": False,
                    "maddrs": [],
                    "layers_loaded": True,
                    "rpc_running": False,
                }

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        api_server.node = DummyNode()
        api_server.client_dht = None
        try:
            result = asyncio.run(api_server.get_nodes())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht

        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["peer_id"], "peer")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertTrue(result["nodes"][0]["layers_loaded"])

    def test_delete_node_unloads_and_clears_global_node(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        dummy = DummyNode()
        original_node = api_server.node
        api_server.node = dummy
        try:
            result = asyncio.run(api_server.delete_node())
            self.assertIsNone(api_server.node)
        finally:
            api_server.node = original_node

        self.assertEqual(result["status"], "deleted")
        self.assertTrue(dummy.stopped)

    def test_incentive_accounting_endpoint_is_simulated_only(self) -> None:
        from api import server as api_server

        class DummyNode:
            def get_accounting_snapshot(self) -> dict:
                return {
                    "peer_id": "peer-123",
                    "model_name": "facebook/opt-125m",
                    "layer_start": 0,
                    "layer_end": 1,
                    "requests_served": 2,
                }

        original_node = api_server.node
        api_server.node = DummyNode()
        try:
            result = asyncio.run(api_server.get_incentive_accounting())
        finally:
            api_server.node = original_node

        self.assertEqual(result["mode"], "simulated")
        self.assertFalse(result["token_ui_enabled"])
        self.assertFalse(result["reward_settlement_enabled"])
        self.assertEqual(result["local_contribution"]["model_name"], "facebook/opt-125m")
        self.assertIn("token_positions_served", result["fields"])

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

    def test_handler_accounting_tracks_successful_forward(self) -> None:
        class AddOneLayer(nn.Module):
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> torch.Tensor:
                return hidden_states + 1

        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=2,
            layer_end=3,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([AddOneLayer()])
        handler._loaded = True

        output = handler.forward(torch.zeros(1, 4, 8))
        snapshot = handler.get_accounting_snapshot()

        self.assertTrue(torch.allclose(output, torch.ones(1, 4, 8)))
        self.assertEqual(snapshot["model_name"], "facebook/opt-125m")
        self.assertEqual(snapshot["layer_start"], 2)
        self.assertEqual(snapshot["layer_end"], 3)
        self.assertEqual(snapshot["requests_served"], 1)
        self.assertEqual(snapshot["failed_requests"], 0)
        self.assertEqual(snapshot["token_positions_served"], 4)
        self.assertGreaterEqual(snapshot["avg_latency_ms"], 0.0)
        self.assertIsNotNone(snapshot["last_success_at"])

    def test_handler_accounting_tracks_failed_forward(self) -> None:
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )

        with self.assertRaisesRegex(RuntimeError, "Layers not loaded"):
            handler.forward(torch.zeros(1, 4, 8))

        snapshot = handler.get_accounting_snapshot()
        self.assertEqual(snapshot["requests_served"], 0)
        self.assertEqual(snapshot["failed_requests"], 1)
        self.assertEqual(snapshot["token_positions_served"], 0)
        self.assertIsNotNone(snapshot["last_error_at"])

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
