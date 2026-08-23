import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import hivemind
import torch
from hivemind.moe.client.remote_expert_worker import RemoteExpertWorker
from transformers import OPTConfig
from transformers.models.opt.modeling_opt import OPTDecoderLayer

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from node.handler import InferenceHandler
from node.session_cache import (
    SessionCacheConfig,
    SessionCacheError,
    SessionCacheManager,
)
from node.session_protocol import (
    decode_session_metadata,
    encode_session_metadata,
    session_operation_document,
    validate_session_operation,
)
from client.generation import DistributedGenerator
from client.sequential import (
    RemoteSequential,
    SessionAmbiguousError,
    SessionPreDispatchError,
    get_peer_expert,
)
from incentives.config import IncentivesConfig
from node.rpc_server import RPCServer


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def tiny_opt_handler() -> InferenceHandler:
    torch.manual_seed(7)
    config = OPTConfig(
        hidden_size=16,
        ffn_dim=32,
        num_attention_heads=4,
        num_hidden_layers=2,
        dropout=0.0,
        attention_dropout=0.0,
        do_layer_norm_before=True,
    )
    handler = InferenceHandler(
        "facebook/opt-125m",
        0,
        2,
        device="cpu",
        dtype=torch.float32,
    )
    handler.layers = torch.nn.ModuleList(
        [OPTDecoderLayer(config, layer_idx=index).eval() for index in range(2)]
    )
    handler._loaded = True
    handler._session_cache_manager = SessionCacheManager(
        layer_count=2,
        hidden_size=16,
        element_size=4,
        model_config=config,
        config=SessionCacheConfig(
            max_sessions=2,
            max_total_bytes=100_000,
            max_session_bytes=100_000,
            max_positions=32,
            ttl_seconds=30,
        ),
        enable_background_cleanup=False,
    )
    return handler


class SessionProtocolTests(unittest.TestCase):
    def test_metadata_round_trip_and_exact_peer_target_validation(self) -> None:
        document = session_operation_document(
            operation="decode",
            session_id="session-one",
            route_id="route-one",
            request_id="request-one",
            operation_id="operation-one",
            peer_id="peer-one",
            rpc_uid="rpc-one",
            layer_start=0,
            layer_end=6,
            position_start=12,
            token_count=1,
        )
        decoded = decode_session_metadata(encode_session_metadata(document))
        validated = validate_session_operation(
            decoded,
            expected_peer_id="peer-one",
            expected_rpc_uid="rpc-one",
            expected_layer_start=0,
            expected_layer_end=6,
        )
        self.assertEqual(validated, document)
        with self.assertRaisesRegex(ValueError, "another peer"):
            validate_session_operation(
                decoded,
                expected_peer_id="peer-two",
                expected_rpc_uid="rpc-one",
                expected_layer_start=0,
                expected_layer_end=6,
            )


class SessionCacheManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.model_config = OPTConfig(
            hidden_size=8,
            ffn_dim=16,
            num_attention_heads=2,
            num_hidden_layers=1,
        )
        self.manager = SessionCacheManager(
            layer_count=1,
            hidden_size=8,
            element_size=4,
            model_config=self.model_config,
            config=SessionCacheConfig(
                max_sessions=2,
                max_total_bytes=4096,
                max_session_bytes=1024,
                max_positions=32,
                ttl_seconds=5,
                operation_history_limit=8,
            ),
            clock=self.clock,
            enable_background_cleanup=False,
        )

    def open(self, session_id: str) -> None:
        self.manager.open(
            session_id=session_id,
            route_id=f"route-{session_id}",
            request_id=f"request-{session_id}",
            operation_id=f"open-{session_id}",
        )

    def execute(self, session_id: str, operation_id: str, position: int) -> None:
        self.manager.execute(
            session_id=session_id,
            route_id=f"route-{session_id}",
            request_id=f"request-{session_id}",
            operation_id=operation_id,
            operation="prefill" if position == 0 else "decode",
            position_start=position,
            token_count=1,
            input_bytes=64,
            target=lambda _cache: torch.zeros((1, 1, 8)),
        )

    def test_admission_eviction_expiry_close_and_replay_are_bounded(self) -> None:
        self.open("one")
        self.execute("one", "prefill-one", 0)
        with self.assertRaisesRegex(SessionCacheError, "already applied"):
            self.execute("one", "prefill-one", 1)

        self.clock.advance(1)
        self.open("two")
        self.clock.advance(1)
        self.open("three")
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["active_sessions"], 2)
        self.assertEqual(snapshot["evicted_sessions"], 1)
        self.assertEqual(snapshot["replay_rejections"], 1)

        self.clock.advance(5)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["active_sessions"], 0)
        self.assertEqual(snapshot["expired_sessions"], 2)

        self.open("four")
        closed = self.manager.close(
            session_id="four",
            route_id="route-four",
            request_id="request-four",
            operation_id="close-four",
        )
        self.assertEqual(closed["state"], "CLOSED")
        self.assertEqual(self.manager.snapshot()["active_sessions"], 0)

    def test_per_session_memory_limit_rejects_before_execution(self) -> None:
        self.open("large")
        executed = False

        def target(_cache):
            nonlocal executed
            executed = True
            return None

        with self.assertRaisesRegex(SessionCacheError, "per-session"):
            self.manager.execute(
                session_id="large",
                route_id="route-large",
                request_id="request-large",
                operation_id="large-prefill",
                operation="prefill",
                position_start=0,
                token_count=32,
                input_bytes=1024,
                target=target,
            )
        self.assertFalse(executed)


class OPTSessionParityTests(unittest.TestCase):
    def test_cached_prefill_decode_matches_stateless_last_position(self) -> None:
        handler = tiny_opt_handler()
        hidden_states = torch.randn((1, 4, 16))
        full_mask = torch.ones((1, 4), dtype=torch.bool)
        full_positions = torch.arange(4).unsqueeze(0)
        stateless = handler.forward(hidden_states, full_mask, full_positions)

        handler.session_open(
            session_id="session-one",
            route_id="route-one",
            request_id="request-one",
            operation_id="open-one",
        )
        handler.session_forward(
            operation="prefill",
            session_id="session-one",
            route_id="route-one",
            request_id="request-one",
            operation_id="prefill-one",
            position_start=0,
            hidden_states=hidden_states[:, :3],
            attention_mask=torch.ones((1, 3), dtype=torch.bool),
            position_ids=full_positions[:, :3],
        )
        decoded, session = handler.session_forward(
            operation="decode",
            session_id="session-one",
            route_id="route-one",
            request_id="request-one",
            operation_id="decode-one",
            position_start=3,
            hidden_states=hidden_states[:, 3:],
            attention_mask=torch.ones((1, 1), dtype=torch.bool),
            position_ids=full_positions[:, 3:],
        )

        torch.testing.assert_close(
            decoded[:, -1],
            stateless[:, -1],
            rtol=1e-5,
            atol=1e-5,
        )
        lm_head = torch.nn.Linear(16, 32, bias=False).eval()
        stateless_logits = lm_head(stateless[:, -1])
        cached_logits = lm_head(decoded[:, -1])
        torch.testing.assert_close(
            cached_logits,
            stateless_logits,
            rtol=1e-5,
            atol=1e-5,
        )
        self.assertEqual(
            int(cached_logits.argmax(dim=-1).item()),
            int(stateless_logits.argmax(dim=-1).item()),
        )
        self.assertEqual(session["expected_position"], 4)
        snapshot = handler.get_session_cache_snapshot()
        self.assertEqual(snapshot["prefill_operations"], 1)
        self.assertEqual(snapshot["decode_operations"], 1)

    def test_decode_input_bytes_are_constant_for_single_position_calls(self) -> None:
        handler = tiny_opt_handler()
        handler.session_open(
            session_id="session-bytes",
            route_id="route-bytes",
            request_id="request-bytes",
            operation_id="open-bytes",
        )
        handler.session_forward(
            operation="prefill",
            session_id="session-bytes",
            route_id="route-bytes",
            request_id="request-bytes",
            operation_id="prefill-bytes",
            position_start=0,
            hidden_states=torch.randn((1, 3, 16)),
            attention_mask=torch.ones((1, 3), dtype=torch.bool),
            position_ids=torch.arange(3).unsqueeze(0),
        )
        one_position_bytes = None
        for position in (3, 4):
            before = handler.get_session_cache_snapshot()["decode_input_bytes"]
            handler.session_forward(
                operation="decode",
                session_id="session-bytes",
                route_id="route-bytes",
                request_id="request-bytes",
                operation_id=f"decode-{position}",
                position_start=position,
                hidden_states=torch.randn((1, 1, 16)),
                attention_mask=torch.ones((1, 1), dtype=torch.bool),
                position_ids=torch.tensor([[position]]),
            )
            after = handler.get_session_cache_snapshot()["decode_input_bytes"]
            delta = after - before
            if one_position_bytes is None:
                one_position_bytes = delta
            self.assertEqual(delta, one_position_bytes)

    def test_worker_unload_releases_all_session_state(self) -> None:
        handler = tiny_opt_handler()
        handler.session_open(
            session_id="session-unload",
            route_id="route-unload",
            request_id="request-unload",
            operation_id="open-unload",
        )
        self.assertEqual(handler.get_session_cache_snapshot()["active_sessions"], 1)
        handler.unload()
        snapshot = handler.get_session_cache_snapshot()
        self.assertFalse(snapshot["supported"])
        self.assertEqual(snapshot["active_sessions"], 0)


class RealHivemindSessionRPCTests(unittest.TestCase):
    def test_real_exact_peer_session_rpc_prefill_decode_and_close(self) -> None:
        handler = tiny_opt_handler()
        handler._session_cache_manager.enable_background_cleanup = True
        full_hidden = torch.randn((1, 4, 16))
        stateless = handler.forward(
            full_hidden,
            torch.ones((1, 4), dtype=torch.bool),
            torch.arange(4).unsqueeze(0),
        )
        dht = hivemind.DHT(
            start=True,
            use_ipfs=False,
            host_maddrs=["/ip4/127.0.0.1/tcp/0"],
        )
        rpc = RPCServer(
            handler,
            dht,
            "sessionintegration",
            incentives_config=IncentivesConfig("off", "", "main"),
        )
        client_dht = None
        expert = None
        try:
            rpc.start()
            peer_id = str(dht.peer_id)
            capability = rpc.get_session_capability()
            self.assertIsNotNone(capability)
            client_dht = hivemind.DHT(
                start=True,
                use_ipfs=False,
                client_mode=True,
                initial_peers=dht.get_visible_maddrs(),
                host_maddrs=["/ip4/127.0.0.1/tcp/0"],
            )
            expert = get_peer_expert(
                client_dht,
                capability["session_rpc_uid"],
                peer_id,
            )

            def call(operation: str, operation_id: str, position: int, hidden):
                token_count = hidden.shape[1] if operation in {"prefill", "decode"} else 0
                document = session_operation_document(
                    operation=operation,
                    session_id="real-session",
                    route_id="real-route",
                    request_id="real-request",
                    operation_id=operation_id,
                    peer_id=peer_id,
                    rpc_uid=capability["session_rpc_uid"],
                    layer_start=0,
                    layer_end=2,
                    position_start=position,
                    token_count=token_count,
                )
                return expert.forward(
                    hidden,
                    encode_session_metadata(document),
                    attention_mask=torch.ones(
                        (1, max(1, token_count)),
                        dtype=torch.bool,
                    ),
                    position_ids=(
                        torch.arange(position, position + token_count).unsqueeze(0)
                        if token_count
                        else torch.zeros((1, 1), dtype=torch.long)
                    ),
                )

            call("open", "open-real", 0, torch.zeros((1, 1, 16)))
            call("prefill", "prefill-real", 0, full_hidden[:, :3])
            decoded, metadata = call(
                "decode",
                "decode-real",
                3,
                full_hidden[:, 3:],
            )
            response = decode_session_metadata(metadata)
            live_cache = rpc.get_session_capability()["session_cache"]
            self.assertEqual(live_cache["active_sessions"], 1)
            self.assertEqual(live_cache["prefill_operations"], 1)
            self.assertEqual(live_cache["decode_operations"], 1)
            call("close", "close-real", 4, torch.zeros((1, 1, 16)))
            closed_cache = rpc.get_session_capability()["session_cache"]
            self.assertEqual(closed_cache["active_sessions"], 0)
            self.assertEqual(closed_cache["closed_sessions"], 1)

            torch.testing.assert_close(
                decoded[:, -1],
                stateless[:, -1],
                rtol=2e-3,
                atol=2e-3,
            )
            self.assertEqual(response["session"]["expected_position"], 4)
            self.assertGreater(response["session"]["estimated_bytes"], 0)
        finally:
            if expert is not None:
                RemoteExpertWorker.run_coroutine(expert.p2p.shutdown())
            if client_dht is not None:
                client_dht.shutdown()
            rpc.stop()
            dht.shutdown()


class FakeSessionExpert:
    def __init__(self, *, fail_forward: bool = False) -> None:
        self.fail_forward = fail_forward
        self.calls: list[dict] = []
        self.expected_position = 0

    def forward(
        self,
        hidden_states: torch.Tensor,
        metadata: torch.Tensor,
        *,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ):
        if self.fail_forward:
            raise RuntimeError("stream reset after dispatch")
        document = decode_session_metadata(metadata)
        self.calls.append(
            {
                "operation": document["operation"],
                "shape": tuple(hidden_states.shape),
                "operation_id": document["operation_id"],
            }
        )
        operation = document["operation"]
        if operation in {"prefill", "decode"}:
            self.assert_position(document)
            self.expected_position += int(document["token_count"])
        elif operation in {"close", "cancel"}:
            self.expected_position = 0
        response = {
            "protocol_version": 1,
            "operation": operation,
            "operation_id": document["operation_id"],
            "session": {
                "expected_position": self.expected_position,
                "estimated_bytes": self.expected_position * 128,
            },
        }
        return hidden_states + (1 if operation in {"prefill", "decode"} else 0), encode_session_metadata(response)

    def assert_position(self, document: dict) -> None:
        if int(document["position_start"]) != self.expected_position:
            raise RuntimeError("position mismatch")


class RemoteSequentialSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.route = [
            {
                "peer_id": "peer-one",
                "rpc_uid": "normal-one",
                "session_rpc_uid": "session-one",
                "session_protocol_version": 1,
                "session_hidden_size": 16,
                "layer_start": 0,
                "layer_end": 1,
            },
            {
                "peer_id": "peer-two",
                "rpc_uid": "normal-two",
                "session_rpc_uid": "session-two",
                "session_protocol_version": 1,
                "session_hidden_size": 16,
                "layer_start": 1,
                "layer_end": 2,
            },
        ]
        self.sequential = RemoteSequential(
            dht=object(),
            dht_prefix="test",
            num_layers=2,
            model_name="synthetic/opt",
        )
        self.sequential._discover_nodes = lambda: [dict(node) for node in self.route]
        self.sequential._build_route_plan = lambda _nodes: {
            "coverage_revision": "coverage",
            "health_revision": "health",
        }
        self.sequential._eligible_attempt_routes = lambda _plan: [
            {"route": [dict(node) for node in self.route]}
        ]
        self.sequential._assert_route_health = lambda _route: None

    def test_exact_route_open_prefill_decode_and_close_use_single_positions(self) -> None:
        experts = {
            "peer-one": FakeSessionExpert(),
            "peer-two": FakeSessionExpert(),
        }

        def resolve(_dht, _uid, peer_id, rpc_role="normal"):
            self.assertEqual(rpc_role, "session")
            return experts[peer_id]

        self.sequential.start_session("session-client")
        with patch("client.sequential.get_ready_peer_expert", side_effect=resolve):
            self.sequential.open_remote_session(16)
            prefill, _trace = self.sequential.session_forward(
                torch.zeros((1, 3, 16)),
                operation="prefill",
                attention_mask=torch.ones((1, 3), dtype=torch.bool),
                position_ids=torch.arange(3).unsqueeze(0),
            )
            decoded, _trace = self.sequential.session_forward(
                torch.zeros((1, 1, 16)),
                operation="decode",
                attention_mask=torch.ones((1, 1), dtype=torch.bool),
                position_ids=torch.tensor([[3]]),
            )
            closed = self.sequential.close_remote_session()

        self.assertTrue(torch.equal(prefill, torch.full_like(prefill, 2)))
        self.assertTrue(torch.equal(decoded, torch.full_like(decoded, 2)))
        self.assertTrue(closed["closed"])
        for expert in experts.values():
            tensor_shapes = [
                call["shape"]
                for call in expert.calls
                if call["operation"] in {"prefill", "decode"}
            ]
            self.assertEqual(tensor_shapes, [(1, 3, 16), (1, 1, 16)])

    def test_preflight_failure_is_safe_but_dispatched_failure_is_ambiguous(self) -> None:
        self.sequential.start_session("session-failure")
        with patch(
            "client.sequential.get_ready_peer_expert",
            side_effect=RuntimeError("expert unavailable"),
        ):
            with self.assertRaises(SessionPreDispatchError):
                self.sequential.open_remote_session(16)

        failing = FakeSessionExpert(fail_forward=True)
        healthy = FakeSessionExpert()
        self.sequential.start_session("session-ambiguous")
        with patch(
            "client.sequential.get_ready_peer_expert",
            side_effect=[failing, healthy, failing, healthy],
        ):
            with self.assertRaises(SessionAmbiguousError):
                self.sequential.open_remote_session(16)
        self.assertEqual(len(failing.calls), 0)

    def test_dispatched_session_failure_retains_exact_hop_diagnostic(self) -> None:
        experts = {
            "peer-one": FakeSessionExpert(),
            "peer-two": FakeSessionExpert(),
        }

        def resolve(_dht, _uid, peer_id, rpc_role="normal"):
            self.assertEqual(rpc_role, "session")
            return experts[peer_id]

        self.sequential.start_session("session-diagnostic")
        with patch("client.sequential.get_ready_peer_expert", side_effect=resolve):
            self.sequential.open_remote_session(16)
            experts["peer-two"].fail_forward = True
            with self.assertRaises(SessionAmbiguousError) as raised:
                self.sequential.session_forward(
                    torch.zeros((1, 2, 16)),
                    operation="prefill",
                    attention_mask=torch.ones((1, 2), dtype=torch.bool),
                    position_ids=torch.arange(2).unsqueeze(0),
                )

        diagnostic = raised.exception.diagnostic
        metrics = self.sequential.get_last_session_metrics()
        self.assertEqual(diagnostic["operation"], "prefill")
        self.assertEqual(diagnostic["hop_index"], 2)
        self.assertEqual(diagnostic["peer_id"], "peer-two")
        self.assertEqual(diagnostic["failure_class"], "ambiguous_transport")
        self.assertEqual(metrics["failure"], diagnostic)
        self.assertEqual(len(metrics["hops"]), 1)
        self.assertEqual(metrics["hops"][0]["peer_id"], "peer-one")

    def test_rebuild_closes_old_route_and_prefills_a_different_route(self) -> None:
        alternate = [
            {
                **node,
                "peer_id": f"alternate-{index}",
                "session_rpc_uid": f"alternate-session-{index}",
            }
            for index, node in enumerate(self.route, start=1)
        ]
        self.sequential._eligible_attempt_routes = lambda _plan: [
            {"route": [dict(node) for node in self.route]},
            {"route": [dict(node) for node in alternate]},
        ]
        experts = {
            node["peer_id"]: FakeSessionExpert()
            for node in [*self.route, *alternate]
        }

        def resolve(_dht, _uid, peer_id, rpc_role="normal"):
            self.assertEqual(rpc_role, "session")
            return experts[peer_id]

        self.sequential.start_session("session-rebuild")
        with patch("client.sequential.get_ready_peer_expert", side_effect=resolve):
            opened = self.sequential.open_remote_session(16)
            rebuilt, _trace = self.sequential.rebuild_remote_session(
                torch.zeros((1, 4, 16)),
                hidden_size=16,
                attention_mask=torch.ones((1, 4), dtype=torch.bool),
                position_ids=torch.arange(4).unsqueeze(0),
            )

        self.assertNotEqual(
            opened["route_id"],
            self.sequential.get_last_session_metrics()["route_id"],
        )
        self.assertTrue(self.sequential.get_last_session_metrics()["rebuilt"])
        self.assertTrue(torch.equal(rebuilt, torch.full_like(rebuilt, 2)))
        for node in self.route:
            self.assertIn(
                "cancel",
                [call["operation"] for call in experts[node["peer_id"]].calls],
            )
        for node in alternate:
            self.assertIn(
                "prefill",
                [call["operation"] for call in experts[node["peer_id"]].calls],
            )


class FakeTokenizer:
    eos_token_id = 999

    def encode(self, _prompt: str, return_tensors: str) -> torch.Tensor:
        if return_tensors != "pt":
            raise AssertionError("unexpected tokenizer transport")
        return torch.tensor([[1, 2, 3]])

    def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
        return "".join(chr(97 + int(token_id) % 26) for token_id in token_ids)


class CapturingSessionSequential:
    def __init__(
        self,
        *,
        fail_first_decode_before_dispatch: bool = False,
        fail_first_decode_ambiguously: bool = False,
    ) -> None:
        self.shapes: list[tuple[int, ...]] = []
        self.operations: list[str] = []
        self.closed = False
        self._metrics = {}
        self.fail_first_decode_before_dispatch = fail_first_decode_before_dispatch
        self.fail_first_decode_ambiguously = fail_first_decode_ambiguously
        self.rebuild_shapes: list[tuple[int, ...]] = []

    def start_session(self, session_id: str) -> str:
        return session_id

    def remote_sessions_available(self) -> bool:
        return True

    def open_remote_session(self, hidden_size: int, cancel_event=None) -> dict:
        if hidden_size != 768:
            raise AssertionError("wrong model hidden size")
        return {"operation": "open"}

    def session_forward(
        self,
        hidden_states: torch.Tensor,
        *,
        operation: str,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        cancel_event=None,
    ):
        if operation == "decode" and self.fail_first_decode_before_dispatch:
            self.fail_first_decode_before_dispatch = False
            raise SessionPreDispatchError("route unavailable before dispatch")
        if operation == "decode" and self.fail_first_decode_ambiguously:
            self.fail_first_decode_ambiguously = False
            raise SessionAmbiguousError("stream reset after dispatch")
        self.shapes.append(tuple(hidden_states.shape))
        self.operations.append(operation)
        input_bytes = sum(
            value.numel() * value.element_size()
            for value in (hidden_states, attention_mask, position_ids)
        )
        self._metrics = {
            "operation": operation,
            "input_bytes": input_bytes,
            "elapsed_ms": 1.0,
            "hops": [{"estimated_cache_bytes": len(self.shapes) * 100}],
        }
        return hidden_states, ["peer"]

    def rebuild_remote_session(
        self,
        hidden_states: torch.Tensor,
        *,
        hidden_size: int,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        cancel_event=None,
    ):
        self.rebuild_shapes.append(tuple(hidden_states.shape))
        self._metrics = {
            "operation": "prefill",
            "input_bytes": sum(
                value.numel() * value.element_size()
                for value in (hidden_states, attention_mask, position_ids)
            ),
            "elapsed_ms": 2.0,
            "hops": [{"estimated_cache_bytes": 400}],
            "rebuilt": True,
        }
        return hidden_states, ["alternate"]

    def get_last_session_metrics(self) -> dict:
        return dict(self._metrics)

    def close_remote_session(self, *, cancelled: bool = False) -> dict:
        self.closed = True
        return {"closed": True, "cancelled": cancelled}

    def end_session(self) -> None:
        return None


class GeneratorSessionIntegrationTests(unittest.TestCase):
    def test_generator_prefills_once_then_sends_one_position_per_decode(self) -> None:
        sequential = CapturingSessionSequential()
        generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential,
            device="cpu",
            dtype=torch.float32,
        )
        generator.tokenizer = FakeTokenizer()
        generator.embed_tokens = torch.nn.Embedding(128, 768)
        generator.norm = torch.nn.Identity()
        generator.lm_head = torch.nn.Linear(768, 128, bias=False)
        generator._loaded = True

        async def collect():
            return [
                chunk
                async for chunk in generator.generate_stream(
                    "hello",
                    max_new_tokens=3,
                    do_sample=False,
                    repetition_penalty=1.0,
                )
            ]

        chunks = asyncio.run(collect())
        self.assertFalse(any("error" in chunk for chunk in chunks))
        self.assertEqual(sequential.operations, ["prefill", "decode", "decode"])
        self.assertEqual(
            sequential.shapes,
            [(1, 3, 768), (1, 1, 768), (1, 1, 768)],
        )
        self.assertTrue(sequential.closed)
        done = next(chunk for chunk in chunks if chunk.get("done"))
        self.assertEqual(done["metrics"]["session_protocol_version"], 1)
        self.assertEqual(done["metrics"]["session_decode_calls"], 2)

    def test_generator_rebuilds_once_from_complete_known_history(self) -> None:
        sequential = CapturingSessionSequential(
            fail_first_decode_before_dispatch=True
        )
        generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential,
            device="cpu",
            dtype=torch.float32,
        )
        generator.tokenizer = FakeTokenizer()
        generator.embed_tokens = torch.nn.Embedding(128, 768)
        generator.norm = torch.nn.Identity()
        generator.lm_head = torch.nn.Linear(768, 128, bias=False)
        generator._loaded = True

        async def collect():
            return [
                chunk
                async for chunk in generator.generate_stream(
                    "hello",
                    max_new_tokens=3,
                    do_sample=False,
                    repetition_penalty=1.0,
                )
            ]

        chunks = asyncio.run(collect())
        self.assertFalse(any("error" in chunk for chunk in chunks))
        self.assertEqual(sequential.rebuild_shapes, [(1, 4, 768)])
        done = next(chunk for chunk in chunks if chunk.get("done"))
        self.assertEqual(done["metrics"]["session_rebuilds"], 1)
        self.assertTrue(done["metrics"]["failed_over"])

    def test_generator_never_rebuilds_after_ambiguous_execution(self) -> None:
        sequential = CapturingSessionSequential(
            fail_first_decode_ambiguously=True
        )
        generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential,
            device="cpu",
            dtype=torch.float32,
        )
        generator.tokenizer = FakeTokenizer()
        generator.embed_tokens = torch.nn.Embedding(128, 768)
        generator.norm = torch.nn.Identity()
        generator.lm_head = torch.nn.Linear(768, 128, bias=False)
        generator._loaded = True

        async def collect():
            return [
                chunk
                async for chunk in generator.generate_stream(
                    "hello",
                    max_new_tokens=3,
                    do_sample=False,
                    repetition_penalty=1.0,
                )
            ]

        chunks = asyncio.run(collect())
        error = next(chunk["error"] for chunk in chunks if "error" in chunk)
        self.assertIn("stream reset after dispatch", error)
        self.assertEqual(sequential.rebuild_shapes, [])
        self.assertTrue(sequential.closed)


if __name__ == "__main__":
    unittest.main()
