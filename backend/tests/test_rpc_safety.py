from __future__ import annotations

import asyncio
import multiprocessing as mp
import tempfile
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
import torch.nn as nn
from hivemind.moe.server import ModuleBackend
from hivemind.utils.tensor_descr import BatchTensorDescriptor

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from node.handler import InferenceHandler
from node.rpc_safety import (
    RPCExecutionTimeout,
    RPCOverloadedError,
    RPCSafetyConfig,
    RPCSafetyController,
    RPCSafetyError,
)
from node.rpc_server import (
    _HandlerModule,
    _ReceiptHandlerModule,
    _install_rejecting_pools,
    _new_rpc_task_future,
)
from incentives.identity import load_application_identity
from incentives.protocol import ProtocolError, encode_metadata_tensor
from incentives.receipts import create_inference_request


def config(**overrides) -> RPCSafetyConfig:
    values = {
        "max_sequence_length": 4,
        "max_batch_size": 2,
        "max_tensor_bytes": 1024,
        "max_metadata_bytes": 16,
        "max_concurrent_forwards": 1,
        "max_queued_forwards": 1,
        "queue_wait_seconds": 0.1,
        "execution_timeout_seconds": 5,
    }
    values.update(overrides)
    return RPCSafetyConfig(**values)


def run_shared_controller(controller: RPCSafetyController) -> None:
    hidden = torch.zeros((1, 2, 8), dtype=torch.float32)
    controller.validate(hidden, None, None)
    controller.execute(hidden, lambda _deadline: hidden)


class RPCSafetyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = RPCSafetyController(config(), hidden_size=8)
        self.hidden = torch.zeros((1, 4, 8), dtype=torch.float32)
        self.mask = torch.ones((1, 4), dtype=torch.long)
        self.positions = torch.arange(4, dtype=torch.long).unsqueeze(0)

    def assert_rejected(self, code: str, hidden=None, mask=None, positions=None) -> None:
        with self.assertRaisesRegex(RPCSafetyError, f"rpc_safety:{code}"):
            self.controller.validate(
                self.hidden if hidden is None else hidden,
                self.mask if mask is None else mask,
                self.positions if positions is None else positions,
            )

    def test_valid_boundary_is_accepted(self) -> None:
        self.controller.validate(self.hidden, self.mask, self.positions)
        self.assertEqual(self.controller.snapshot()["rejected_requests"], 0)

    def test_rank_batch_sequence_hidden_dtype_and_finite_values_are_bounded(self) -> None:
        cases = [
            ("shape", torch.zeros((4, 8))),
            ("batch", torch.zeros((3, 4, 8))),
            ("sequence", torch.zeros((1, 5, 8))),
            ("hidden_size", torch.zeros((1, 4, 7))),
            ("dtype", torch.zeros((1, 4, 8), dtype=torch.int64)),
        ]
        for code, value in cases:
            with self.subTest(code=code):
                self.assert_rejected(code, hidden=value)
        non_finite = self.hidden.clone()
        non_finite[0, 0, 0] = float("nan")
        self.assert_rejected("non_finite", hidden=non_finite)

    def test_combined_tensor_bytes_are_bounded(self) -> None:
        controller = RPCSafetyController(
            config(max_sequence_length=3, max_tensor_bytes=1024),
            hidden_size=82,
        )
        hidden = torch.zeros((1, 3, 82), dtype=torch.float32)
        large_mask = torch.zeros((1, 1, 3, 3), dtype=torch.float32)
        positions = torch.arange(3).unsqueeze(0)
        controller.validate(hidden, None, positions)
        with self.assertRaisesRegex(RPCSafetyError, "tensor_bytes"):
            controller.validate(hidden, large_mask, positions)

    def test_masks_and_positions_require_consistent_safe_values(self) -> None:
        bad_masks = [
            torch.ones((1, 3), dtype=torch.long),
            torch.full((1, 4), 2, dtype=torch.long),
            torch.full((1, 4), float("nan"), dtype=torch.float32),
        ]
        for value in bad_masks:
            with self.subTest(shape=tuple(value.shape), dtype=str(value.dtype)):
                self.assert_rejected("mask", mask=value)

        bad_positions = [
            torch.arange(3).unsqueeze(0),
            torch.arange(4, dtype=torch.float32).unsqueeze(0),
            torch.tensor([[0, 1, 2, -1]]),
            torch.tensor([[0, 1, 2, 4]]),
        ]
        for value in bad_positions:
            with self.subTest(shape=tuple(value.shape), dtype=str(value.dtype)):
                self.assert_rejected("position_ids", positions=value)

    def test_receipt_metadata_length_is_rejected_before_json_or_signature_work(self) -> None:
        metadata = torch.zeros((1, 32), dtype=torch.uint8)
        metadata[0, :4] = torch.tensor(list(struct.pack(">I", 17)), dtype=torch.uint8)
        with self.assertRaisesRegex(RPCSafetyError, "metadata"):
            self.controller.validate(self.hidden, self.mask, self.positions, metadata)

    def test_rejection_counters_are_grouped_without_payload_data(self) -> None:
        self.assert_rejected("sequence", hidden=torch.zeros((1, 5, 8)))
        self.assert_rejected("dtype", hidden=torch.zeros((1, 4, 8), dtype=torch.int64))
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["rejected_requests"], 2)
        self.assertEqual(snapshot["rejected_by_reason"]["sequence"], 1)
        self.assertEqual(snapshot["rejected_by_reason"]["dtype"], 1)
        self.assertNotIn("hidden_states", snapshot)

    def test_malformed_metadata_frames_fail_deterministically(self) -> None:
        class Handler:
            model_name = "test/model"
            layer_start = 0
            layer_end = 1
            calls = 0

            def forward(self, hidden_states, **_kwargs):
                self.calls += 1
                return hidden_states

        handler = Handler()
        module = _ReceiptHandlerModule(
            handler,
            SimpleNamespace(public_key="unused"),
            "peer",
            "rpc.0.1",
            "main",
            self.controller,
        )
        for length in range(1, 17):
            metadata = torch.zeros((1, 32), dtype=torch.uint8)
            metadata[0, :4] = torch.tensor(list(struct.pack(">I", length)), dtype=torch.uint8)
            metadata[0, 4 : 4 + length] = 255
            with self.subTest(length=length):
                with self.assertRaises((ProtocolError, UnicodeDecodeError, ValueError)):
                    module(self.hidden, metadata, self.mask, self.positions)
        self.assertEqual(handler.calls, 0)


class RPCSafetyAdmissionTests(unittest.TestCase):
    def test_rpc_task_future_requires_an_active_event_loop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "event_loop_unavailable"):
            _new_rpc_task_future("test-pool")

    def test_rpc_task_future_repairs_an_unbound_hivemind_future(self) -> None:
        class UnboundFuture:
            _loop = None
            _aio_event = None

        async def create_future():
            running_loop = asyncio.get_running_loop()
            with patch("node.rpc_server.MPFuture", UnboundFuture):
                future = _new_rpc_task_future("test-pool")
            return future, running_loop

        future, running_loop = asyncio.run(create_future())
        self.assertIs(future._loop, running_loop)
        self.assertIsNotNone(future._aio_event)

    def test_hivemind_task_pool_rejects_full_queue_without_blocking(self) -> None:
        controller = RPCSafetyController(config(), hidden_size=8)
        descriptor = BatchTensorDescriptor(8)
        backend = ModuleBackend(
            "safetytest.0.1",
            nn.Identity(),
            args_schema=(descriptor,),
            kwargs_schema={},
            outputs_schema=(descriptor,),
            max_batch_size=2,
            pool_size=1,
        )
        _install_rejecting_pools(backend, controller)

        async def submit_tasks():
            return (
                backend.forward_pool.submit_task(torch.zeros((1, 8))),
                backend.forward_pool.submit_task(torch.zeros((1, 8))),
            )

        first, second = asyncio.run(submit_tasks())
        try:
            with self.assertRaisesRegex(RPCOverloadedError, "queue capacity"):
                second.result(timeout=1)
            self.assertEqual(
                controller.snapshot()["rejected_by_reason"]["overloaded"], 1
            )
        finally:
            first.cancel()
            backend.forward_pool.tasks.close()
            backend.backward_pool.tasks.close()

    def test_saturation_bounds_active_and_queued_work_and_returns_permits(self) -> None:
        controller = RPCSafetyController(config(queue_wait_seconds=1), hidden_size=8)
        hidden = torch.zeros((1, 2, 8))
        first_started = threading.Event()
        release_first = threading.Event()
        results: list[str] = []

        def first_target(_deadline: float) -> torch.Tensor:
            first_started.set()
            release_first.wait(2)
            return hidden

        first = threading.Thread(
            target=lambda: (
                controller.execute(hidden, first_target),
                results.append("first"),
            )
        )
        second = threading.Thread(
            target=lambda: (
                controller.execute(hidden, lambda _deadline: hidden),
                results.append("second"),
            )
        )
        first.start()
        self.assertTrue(first_started.wait(1))
        second.start()
        deadline = time.monotonic() + 1
        while controller.snapshot()["queued_forwards"] != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.assertRaisesRegex(RPCOverloadedError, "overloaded"):
            controller.execute(hidden, lambda _deadline: hidden)

        release_first.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["peak_active_forwards"], 1)
        self.assertEqual(snapshot["peak_queued_forwards"], 1)
        self.assertEqual(snapshot["active_forwards"], 0)
        self.assertEqual(snapshot["queued_forwards"], 0)
        self.assertEqual(snapshot["rejected_by_reason"]["overloaded"], 1)
        self.assertEqual(set(results), {"first", "second"})

        controller.execute(hidden, lambda _deadline: hidden)
        self.assertEqual(controller.snapshot()["completed_requests"], 3)

    def test_queue_timeout_releases_waiting_state(self) -> None:
        controller = RPCSafetyController(config(queue_wait_seconds=0.05), hidden_size=8)
        hidden = torch.zeros((1, 2, 8))
        started = threading.Event()
        release = threading.Event()

        def target(_deadline: float) -> torch.Tensor:
            started.set()
            release.wait(1)
            return hidden

        active = threading.Thread(target=lambda: controller.execute(hidden, target))
        active.start()
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(RPCOverloadedError, "queue_timeout"):
            controller.execute(hidden, lambda _deadline: hidden)
        self.assertEqual(controller.snapshot()["queued_forwards"], 0)
        release.set()
        active.join(1)

    def test_timeout_has_no_completion_and_releases_permit(self) -> None:
        controller = RPCSafetyController(config(), hidden_size=8)
        hidden = torch.zeros((1, 2, 8))
        with self.assertRaises(RPCExecutionTimeout):
            controller.execute(
                hidden,
                lambda _deadline: (_ for _ in ()).throw(RPCExecutionTimeout("deadline")),
            )
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["completed_requests"], 0)
        self.assertEqual(snapshot["timed_out_requests"], 1)
        self.assertEqual(snapshot["active_forwards"], 0)
        controller.execute(hidden, lambda _deadline: hidden)
        self.assertEqual(controller.snapshot()["completed_requests"], 1)

    def test_handler_exception_releases_permit_without_completion(self) -> None:
        controller = RPCSafetyController(config(), hidden_size=8)
        hidden = torch.zeros((1, 2, 8))
        with self.assertRaisesRegex(RuntimeError, "model failure"):
            controller.execute(
                hidden,
                lambda _deadline: (_ for _ in ()).throw(RuntimeError("model failure")),
            )
        self.assertEqual(controller.snapshot()["active_forwards"], 0)
        self.assertEqual(controller.snapshot()["failed_requests"], 1)
        controller.execute(hidden, lambda _deadline: hidden)
        self.assertEqual(controller.snapshot()["completed_requests"], 1)

    def test_counters_are_shared_with_runtime_process(self) -> None:
        controller = RPCSafetyController(config(), hidden_size=8)
        process = mp.Process(target=run_shared_controller, args=(controller,))
        process.start()
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["accepted_requests"], 1)
        self.assertEqual(snapshot["completed_requests"], 1)


class RPCSafetyWrapperTests(unittest.TestCase):
    def test_legacy_wrapper_rejects_before_handler_execution(self) -> None:
        class Handler:
            layer_start = 0
            layer_end = 1

            def __init__(self) -> None:
                self.calls = 0

            def is_loaded(self) -> bool:
                return True

            def forward(self, hidden_states, **_kwargs):
                self.calls += 1
                return hidden_states

        handler = Handler()
        module = _HandlerModule(handler, RPCSafetyController(config(), hidden_size=8))
        with self.assertRaisesRegex(RPCSafetyError, "sequence"):
            module(torch.zeros((1, 5, 8)))
        self.assertEqual(handler.calls, 0)

    def test_receipt_wrapper_rejects_metadata_before_identity_or_model_work(self) -> None:
        class Handler:
            model_name = "test/model"
            layer_start = 0
            layer_end = 1

            def __init__(self) -> None:
                self.calls = 0

            def forward(self, hidden_states, **_kwargs):
                self.calls += 1
                return hidden_states

        handler = Handler()
        module = _ReceiptHandlerModule(
            handler,
            SimpleNamespace(public_key="unused"),
            "peer",
            "rpc.0.1",
            "main",
            RPCSafetyController(config(), hidden_size=8),
        )
        metadata = torch.zeros((1, 32), dtype=torch.uint8)
        metadata[0, :4] = torch.tensor(list(struct.pack(">I", 17)), dtype=torch.uint8)
        with self.assertRaisesRegex(RPCSafetyError, "metadata"):
            module(torch.zeros((1, 2, 8)), metadata)
        self.assertEqual(handler.calls, 0)

    def test_handler_cooperative_deadline_records_failure_not_success(self) -> None:
        handler = InferenceHandler("facebook/opt-125m", 0, 1, device="cpu")

        class Layer(nn.Module):
            def forward(self, hidden_states, **_kwargs):
                return hidden_states

        handler.layers = nn.ModuleList([Layer()])
        handler._loaded = True
        with self.assertRaises(RPCExecutionTimeout):
            handler.forward(
                torch.zeros((1, 2, 8)),
                deadline=time.monotonic() - 1,
            )
        accounting = handler.get_accounting_snapshot()
        self.assertEqual(accounting["requests_served"], 0)
        self.assertEqual(accounting["failed_requests"], 1)

    def test_receipt_failure_after_model_work_commits_no_success_or_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = load_application_identity(root / "worker.json")
            generator = load_application_identity(root / "generator.json")
            handler = InferenceHandler("test/model", 0, 1, device="cpu")

            class Layer(nn.Module):
                def forward(self, hidden_states, **_kwargs):
                    return hidden_states + 1

            handler.layers = nn.ModuleList([Layer()])
            handler._loaded = True
            hidden = torch.zeros((1, 2, 8), dtype=torch.float32)
            route = [
                {
                    "peer_id": "worker-peer",
                    "application_public_key": worker.public_key,
                    "rpc_uid": "rpc.0.1",
                    "layer_start": 0,
                    "layer_end": 1,
                }
            ]
            request = create_inference_request(
                generator,
                generator_peer_id="generator-peer",
                session_id="session",
                model_name="test/model",
                model_revision="main",
                route=route,
                worker=route[0],
                hidden_states=hidden,
                position_count=2,
                request_id="request",
            )
            safety = RPCSafetyController(config(max_metadata_bytes=16_380), hidden_size=8)
            module = _ReceiptHandlerModule(
                handler,
                worker,
                "worker-peer",
                "rpc.0.1",
                "main",
                safety,
            )
            with patch(
                "node.rpc_server.create_worker_receipt",
                side_effect=RuntimeError("receipt signing failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "receipt signing failed"):
                    module(hidden, encode_metadata_tensor(request))

            accounting = handler.get_accounting_snapshot()
            self.assertEqual(accounting["requests_served"], 0)
            self.assertEqual(accounting["failed_requests"], 0)
            self.assertEqual(safety.snapshot()["completed_requests"], 0)


if __name__ == "__main__":
    unittest.main()
