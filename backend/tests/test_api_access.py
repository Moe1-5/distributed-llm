import copy
import asyncio
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

from incentives.access import (
    AccessConfig,
    AccessError,
    ApiAccessManager,
    ApiAccessStore,
)
from incentives.identity import load_application_identity


class ApiAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = ApiAccessStore(root / "access.sqlite3")
        self.identity = load_application_identity(root / "identity.json")
        self.config = AccessConfig(
            mode="enforced",
            database_path=root / "access.sqlite3",
            price_scale=1,
            capability_ttl_seconds=60,
        )
        self.manager = ApiAccessManager(self.config, self.store, self.identity)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_key_creation_requires_verified_credits_and_stores_only_hash(self) -> None:
        with self.assertRaisesRegex(AccessError, "positive verified"):
            self.store.create_key(self.identity.public_key, "Exam demo", verified_credits=0)

        created = self.store.create_key(
            self.identity.public_key,
            "Exam demo",
            verified_credits=10,
        )
        database_bytes = self.store.path.read_bytes()

        self.assertNotIn(created["api_key"].encode("utf-8"), database_bytes)
        self.assertEqual(
            self.store.authenticate(created["api_key"])["key_id"],
            created["key_id"],
        )

    def test_revoked_key_cannot_authenticate(self) -> None:
        created = self.store.create_key(
            self.identity.public_key,
            "Revocable",
            verified_credits=1,
        )
        self.assertTrue(self.store.revoke_key(self.identity.public_key, created["key_id"]))

        with self.assertRaisesRegex(AccessError, "revoked"):
            self.store.authenticate(created["api_key"])

    def test_enforced_reservations_share_balance_across_keys(self) -> None:
        first = self.store.create_key(
            self.identity.public_key,
            "First",
            verified_credits=10,
        )
        second = self.store.create_key(
            self.identity.public_key,
            "Second",
            verified_credits=10,
        )
        self.store.reserve(
            key_id=first["key_id"],
            model_name="facebook/opt-125m",
            estimated_positions=6,
            verified_credits=10,
            mode="enforced",
            price_scale=1,
            request_id="request-one",
        )

        with self.assertRaisesRegex(AccessError, "4 are available"):
            self.store.reserve(
                key_id=second["key_id"],
                model_name="facebook/opt-125m",
                estimated_positions=5,
                verified_credits=10,
                mode="enforced",
                price_scale=1,
                request_id="request-two",
            )

    def test_concurrent_reservations_cannot_double_spend(self) -> None:
        key = self.store.create_key(
            self.identity.public_key,
            "Concurrent",
            verified_credits=10,
        )
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def reserve(request_id: str) -> None:
            barrier.wait()
            try:
                self.store.reserve(
                    key_id=key["key_id"],
                    model_name="facebook/opt-125m",
                    estimated_positions=7,
                    verified_credits=10,
                    mode="enforced",
                    price_scale=1,
                    request_id=request_id,
                )
                outcomes.append("reserved")
            except AccessError as exc:
                outcomes.append(exc.code)

        threads = [
            threading.Thread(target=reserve, args=("concurrent-a",)),
            threading.Thread(target=reserve, args=("concurrent-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertCountEqual(outcomes, ["reserved", "insufficient_credits"])

    def test_completion_is_idempotent_and_failure_releases_reservation(self) -> None:
        key = self.store.create_key(
            self.identity.public_key,
            "Usage",
            verified_credits=20,
        )
        self.store.reserve(
            key_id=key["key_id"],
            model_name="facebook/opt-125m",
            estimated_positions=10,
            verified_credits=20,
            mode="enforced",
            price_scale=1,
            request_id="completed",
        )
        first = self.store.complete("completed", actual_positions=7, price_scale=1)
        second = self.store.complete("completed", actual_positions=7, price_scale=1)
        self.store.reserve(
            key_id=key["key_id"],
            model_name="facebook/opt-125m",
            estimated_positions=5,
            verified_credits=20,
            mode="enforced",
            price_scale=1,
            request_id="failed",
        )

        self.assertTrue(self.store.release("failed"))
        self.assertEqual(first["charged_units"], 7)
        self.assertEqual(second["charged_units"], 7)
        self.assertEqual(
            self.store.usage_summary(key["key_id"]),
            {"spent_units": 7, "reserved_units": 0},
        )

    def test_shadow_mode_records_usage_without_spending(self) -> None:
        key = self.store.create_key(
            self.identity.public_key,
            "Shadow",
            verified_credits=1,
        )
        reservation = self.store.reserve(
            key_id=key["key_id"],
            model_name="facebook/opt-125m",
            estimated_positions=100,
            verified_credits=0,
            mode="shadow",
            price_scale=1,
        )
        result = self.store.complete(
            reservation["request_id"],
            actual_positions=50,
            price_scale=1,
        )

        self.assertEqual(result["charged_units"], 0)
        self.assertEqual(result["projected_units"], 50)

    def test_signed_capability_rejects_tampering_expiry_oversize_and_replay(self) -> None:
        capability = self.manager.issue_capability(
            request_id="capability-request",
            key_id="key-id",
            model_name="facebook/opt-125m",
            max_positions=20,
            now=100,
        )
        oversized = self.manager.issue_capability(
            request_id="oversized-request",
            key_id="key-id",
            model_name="facebook/opt-125m",
            max_positions=10,
            now=100,
        )
        expired = self.manager.issue_capability(
            request_id="expired-request",
            key_id="key-id",
            model_name="facebook/opt-125m",
            max_positions=10,
            now=100,
        )
        tampered = copy.deepcopy(capability)
        tampered["payload"]["model_name"] = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"

        with self.assertRaisesRegex(AccessError, "signature"):
            self.manager.verify_and_consume_capability(
                tampered,
                request_id="capability-request",
                model_name="facebook/opt-125m",
                positions=10,
                now=110,
            )
        with self.assertRaisesRegex(AccessError, "position limit"):
            self.manager.verify_and_consume_capability(
                oversized,
                request_id="oversized-request",
                model_name="facebook/opt-125m",
                positions=11,
                now=110,
            )
        with self.assertRaisesRegex(AccessError, "expired"):
            self.manager.verify_and_consume_capability(
                expired,
                request_id="expired-request",
                model_name="facebook/opt-125m",
                positions=5,
                now=161,
            )

        accepted = self.manager.verify_and_consume_capability(
            capability,
            request_id="capability-request",
            model_name="facebook/opt-125m",
            positions=10,
            now=110,
        )
        self.assertEqual(accepted["api_key_id"], "key-id")
        with self.assertRaisesRegex(AccessError, "already consumed"):
            self.manager.verify_and_consume_capability(
                capability,
                request_id="capability-request",
                model_name="facebook/opt-125m",
                positions=10,
                now=111,
            )

    def test_openai_compatible_endpoint_authenticates_and_charges_completed_usage(self) -> None:
        from api import server as api_server

        created = self.store.create_key(
            self.identity.public_key,
            "OpenAI client",
            verified_credits=100,
        )

        class FakeTokenizer:
            @staticmethod
            def apply_chat_template(messages, **kwargs) -> str:
                return "user: hello\nassistant:"

            @staticmethod
            def encode(prompt, **kwargs) -> list[int]:
                return [1, 2, 3]

        class FakeGenerator:
            model_name = "facebook/opt-125m"
            tokenizer = FakeTokenizer()
            sequential = type(
                "Sequential",
                (),
                {
                    "get_health_readiness": staticmethod(
                        lambda: {
                            "enabled": True,
                            "route_ready": True,
                            "reasons": [],
                            "selected_route": [],
                        }
                    )
                },
            )()

            @staticmethod
            def is_loaded() -> bool:
                return True

            @staticmethod
            async def generate_stream(**kwargs):
                yield {"token": "hello"}
                yield {
                    "done": True,
                    "metrics": {"generated_tokens": 2, "stopped": False},
                }

        original_generator = api_server.generator
        original_manager_getter = api_server.get_api_access_manager
        original_credit_snapshot = api_server._verified_credit_snapshot

        async def credits() -> dict:
            return {"verified_credits": 100}

        api_server.generator = FakeGenerator()
        api_server.get_api_access_manager = lambda: self.manager
        api_server._verified_credit_snapshot = credits
        try:
            response = asyncio.run(
                api_server.openai_chat_completions(
                    api_server.OpenAIChatCompletionRequest(
                        model="facebook/opt-125m",
                        messages=[api_server.OpenAIChatMessage(role="user", content="hello")],
                        max_tokens=4,
                    ),
                    authorization=f"Bearer {created['api_key']}",
                    x_request_id="openai-request",
                )
            )
        finally:
            api_server.generator = original_generator
            api_server.get_api_access_manager = original_manager_getter
            api_server._verified_credit_snapshot = original_credit_snapshot

        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["choices"][0]["message"]["content"], "hello")
        self.assertEqual(response["usage"]["total_tokens"], 5)
        self.assertEqual(response["usage"]["credit_units"], 5)

    def test_rejected_second_stream_does_not_stop_the_active_owner(self) -> None:
        from api import server as api_server
        from client.generation import DistributedGenerator

        created = self.store.create_key(
            self.identity.public_key,
            "Concurrent OpenAI client",
            verified_credits=100,
        )

        class FakeTokenizer:
            @staticmethod
            def apply_chat_template(messages, **kwargs) -> str:
                return "user: hello\nassistant:"

            @staticmethod
            def encode(prompt, **kwargs) -> list[int]:
                return [1, 2, 3]

        class ReadySequential:
            @staticmethod
            def get_health_readiness() -> dict:
                return {
                    "enabled": True,
                    "route_ready": True,
                    "reasons": [],
                    "selected_route": [],
                }

        active_generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential=ReadySequential(),
        )
        active_generator._loaded = True
        active_generator.tokenizer = FakeTokenizer()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        owned_calls = 0

        async def blocking_owned_stream(**_kwargs):
            nonlocal owned_calls
            owned_calls += 1
            first_started.set()
            await release_first.wait()
            yield {"token": "hello"}
            yield {
                "done": True,
                "metrics": {"generated_tokens": 1, "stopped": False},
            }

        active_generator._generate_stream_owned = blocking_owned_stream
        original_generator = api_server.generator
        original_manager_getter = api_server.get_api_access_manager
        original_credit_snapshot = api_server._verified_credit_snapshot

        async def credits() -> dict:
            return {"verified_credits": 100}

        async def exercise_streams() -> tuple[list[str], list[str]]:
            request = api_server.OpenAIChatCompletionRequest(
                model="facebook/opt-125m",
                messages=[api_server.OpenAIChatMessage(role="user", content="hello")],
                max_tokens=4,
                stream=True,
            )
            response_a = await api_server.openai_chat_completions(
                request,
                authorization=f"Bearer {created['api_key']}",
                x_request_id="stream-owner-a",
            )
            response_b = await api_server.openai_chat_completions(
                request,
                authorization=f"Bearer {created['api_key']}",
                x_request_id="stream-rejected-b",
            )
            iterator_a = response_a.body_iterator
            first_a = asyncio.create_task(iterator_a.__anext__())
            await asyncio.wait_for(first_started.wait(), timeout=1)
            rejected = [item async for item in response_b.body_iterator]
            self.assertFalse(active_generator._stop_event.is_set())
            self.assertEqual(
                list(active_generator._active_operations.values()),
                ["generate"],
            )
            release_first.set()
            accepted = [await asyncio.wait_for(first_a, timeout=1)]
            accepted.extend([item async for item in iterator_a])
            return accepted, rejected

        api_server.generator = active_generator
        api_server.get_api_access_manager = lambda: self.manager
        api_server._verified_credit_snapshot = credits
        try:
            accepted, rejected = asyncio.run(exercise_streams())
        finally:
            release_first.set()
            api_server.generator = original_generator
            api_server.get_api_access_manager = original_manager_getter
            api_server._verified_credit_snapshot = original_credit_snapshot

        self.assertEqual(owned_calls, 1)
        self.assertIn("hello", "".join(accepted))
        self.assertIn("generator_busy", "".join(rejected))
        self.assertIn("[DONE]", "".join(rejected))
        self.assertFalse(active_generator._stop_event.is_set())
        self.assertEqual(active_generator._active_operations, {})
        self.assertEqual(
            self.store.usage_summary(created["key_id"]),
            {"spent_units": 4, "reserved_units": 0},
        )

    def test_stream_error_closes_generation_owner_before_response_finishes(self) -> None:
        from api import server as api_server
        from client.generation import DistributedGenerator

        created = self.store.create_key(
            self.identity.public_key,
            "Failing OpenAI stream",
            verified_credits=100,
        )

        class FakeTokenizer:
            @staticmethod
            def apply_chat_template(messages, **kwargs) -> str:
                return "user: hello\nassistant:"

            @staticmethod
            def encode(prompt, **kwargs) -> list[int]:
                return [1, 2, 3]

        class ReadySequential:
            @staticmethod
            def get_health_readiness() -> dict:
                return {
                    "enabled": True,
                    "route_ready": True,
                    "reasons": [],
                    "selected_route": [],
                }

        active_generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential=ReadySequential(),
        )
        active_generator._loaded = True
        active_generator.tokenizer = FakeTokenizer()
        owned_cleanup: list[str] = []

        async def failing_owned_stream(**_kwargs):
            try:
                yield {"error": "route failed"}
            finally:
                owned_cleanup.append("closed")

        active_generator._generate_stream_owned = failing_owned_stream
        original_generator = api_server.generator
        original_manager_getter = api_server.get_api_access_manager
        original_credit_snapshot = api_server._verified_credit_snapshot

        async def credits() -> dict:
            return {"verified_credits": 100}

        async def exercise_error_stream() -> list[str]:
            response = await api_server.openai_chat_completions(
                api_server.OpenAIChatCompletionRequest(
                    model="facebook/opt-125m",
                    messages=[
                        api_server.OpenAIChatMessage(role="user", content="hello")
                    ],
                    max_tokens=4,
                    stream=True,
                ),
                authorization=f"Bearer {created['api_key']}",
                x_request_id="stream-error-owner",
            )
            events = [item async for item in response.body_iterator]
            self.assertEqual(active_generator._active_operations, {})
            self.assertEqual(owned_cleanup, ["closed"])
            return events

        api_server.generator = active_generator
        api_server.get_api_access_manager = lambda: self.manager
        api_server._verified_credit_snapshot = credits
        try:
            events = asyncio.run(exercise_error_stream())
        finally:
            api_server.generator = original_generator
            api_server.get_api_access_manager = original_manager_getter
            api_server._verified_credit_snapshot = original_credit_snapshot

        self.assertIn("route failed", "".join(events))
        self.assertIn("[DONE]", "".join(events))
        self.assertEqual(
            self.store.usage_summary(created["key_id"]),
            {"spent_units": 0, "reserved_units": 0},
        )

    def test_stream_consumer_close_releases_generation_owner_immediately(self) -> None:
        from api import server as api_server
        from client.generation import DistributedGenerator

        created = self.store.create_key(
            self.identity.public_key,
            "Disconnecting OpenAI stream",
            verified_credits=100,
        )

        class FakeTokenizer:
            @staticmethod
            def apply_chat_template(messages, **kwargs) -> str:
                return "user: hello\nassistant:"

            @staticmethod
            def encode(prompt, **kwargs) -> list[int]:
                return [1, 2, 3]

        class ReadySequential:
            @staticmethod
            def get_health_readiness() -> dict:
                return {
                    "enabled": True,
                    "route_ready": True,
                    "reasons": [],
                    "selected_route": [],
                }

        active_generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential=ReadySequential(),
        )
        active_generator._loaded = True
        active_generator.tokenizer = FakeTokenizer()
        owned_cleanup: list[str] = []

        async def streaming_owned(**_kwargs):
            try:
                yield {"token": "hello"}
                await asyncio.Event().wait()
            finally:
                owned_cleanup.append("closed")

        active_generator._generate_stream_owned = streaming_owned
        original_generator = api_server.generator
        original_manager_getter = api_server.get_api_access_manager
        original_credit_snapshot = api_server._verified_credit_snapshot

        async def credits() -> dict:
            return {"verified_credits": 100}

        async def exercise_disconnect() -> str:
            response = await api_server.openai_chat_completions(
                api_server.OpenAIChatCompletionRequest(
                    model="facebook/opt-125m",
                    messages=[
                        api_server.OpenAIChatMessage(role="user", content="hello")
                    ],
                    max_tokens=4,
                    stream=True,
                ),
                authorization=f"Bearer {created['api_key']}",
                x_request_id="stream-disconnect-owner",
            )
            iterator = response.body_iterator
            first = await iterator.__anext__()
            await iterator.aclose()
            self.assertEqual(active_generator._active_operations, {})
            self.assertEqual(owned_cleanup, ["closed"])
            return first

        api_server.generator = active_generator
        api_server.get_api_access_manager = lambda: self.manager
        api_server._verified_credit_snapshot = credits
        try:
            first_event = asyncio.run(exercise_disconnect())
        finally:
            api_server.generator = original_generator
            api_server.get_api_access_manager = original_manager_getter
            api_server._verified_credit_snapshot = original_credit_snapshot

        self.assertIn("hello", first_event)
        self.assertEqual(
            self.store.usage_summary(created["key_id"]),
            {"spent_units": 0, "reserved_units": 0},
        )

    def test_developer_key_management_rejects_nonlocal_browser_origin(self) -> None:
        from api import server as api_server

        with self.assertRaises(HTTPException) as raised:
            api_server._require_local_management_origin(
                SimpleNamespace(headers={"origin": "https://example.com"})
            )

        self.assertEqual(raised.exception.status_code, 403)
        api_server._require_local_management_origin(
            SimpleNamespace(headers={"origin": "http://localhost:5173"})
        )


if __name__ == "__main__":
    unittest.main()
