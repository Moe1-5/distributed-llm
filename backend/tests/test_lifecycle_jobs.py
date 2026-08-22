import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from api.lifecycle_jobs import LifecycleJobStore


def wait_for_terminal(store: LifecycleJobStore, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = store.get(job_id)
        if snapshot is not None and snapshot["status"] in {"ready", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Lifecycle job {job_id} did not finish")


class LifecycleJobStoreTests(unittest.TestCase):
    def test_job_reports_progress_and_preserves_result(self) -> None:
        store = LifecycleJobStore()

        def target(progress, cancelled):
            progress("loading", "Loading test runtime.")
            return {"status": "ready", "value": 7}

        submitted = store.submit("generator_start", "generator:test", target)
        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertEqual(completed["status"], "ready")
        self.assertEqual(completed["stage"], "ready")
        self.assertEqual(completed["result"], {"status": "ready", "value": 7})
        self.assertGreaterEqual(completed["elapsed_seconds"], 0)

    def test_active_resource_submission_is_deduplicated(self) -> None:
        store = LifecycleJobStore()
        release = threading.Event()

        def target(progress, cancelled):
            progress("loading", "Waiting in test.")
            release.wait(1)
            return {"status": "ready"}

        first = store.submit("node_start", "node:test", target)
        second = store.submit("node_start", "node:test", target)
        release.set()

        self.assertEqual(second["job_id"], first["job_id"])
        self.assertTrue(second["reused"])
        wait_for_terminal(store, first["job_id"])

    def test_cancellation_request_reaches_owned_target(self) -> None:
        store = LifecycleJobStore()

        def target(progress, cancelled):
            progress("loading", "Waiting for cancellation.")
            self.assertTrue(cancelled.wait(1))
            return {"status": "cancelled"}

        submitted = store.submit("node_start", "node:cancel", target)
        cancelled = store.cancel(submitted["job_id"])
        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertIsNotNone(cancelled)
        self.assertTrue(cancelled["cancel_requested"])
        self.assertEqual(completed["status"], "cancelled")

    def test_late_cancel_preserves_a_committed_ready_result(self) -> None:
        store = LifecycleJobStore()
        target_returned = threading.Event()
        release_result_read = threading.Event()

        class CommittedResult(dict):
            def get(self, key, default=None):
                if key == "status":
                    target_returned.set()
                    if not release_result_read.wait(1):
                        raise TimeoutError("test did not release result inspection")
                return super().get(key, default)

        submitted = store.submit(
            "generator_start",
            "generator:late-cancel",
            lambda progress, cancelled: CommittedResult(
                status="ready",
                runtime_id="committed-generator",
            ),
        )
        self.assertTrue(target_returned.wait(1))

        cancellation = store.cancel(submitted["job_id"])
        release_result_read.set()
        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertIsNotNone(cancellation)
        self.assertTrue(completed["cancel_requested"])
        self.assertEqual(completed["status"], "ready")
        self.assertEqual(completed["stage"], "ready")
        self.assertEqual(completed["result"]["runtime_id"], "committed-generator")
        self.assertIn("committed before", completed["detail"])

    def test_error_result_becomes_failed_job_with_actionable_message(self) -> None:
        store = LifecycleJobStore()
        submitted = store.submit(
            "generator_start",
            "generator:error",
            lambda progress, cancelled: {
                "status": "error",
                "error": "relay unavailable",
            },
        )

        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["error"], "relay unavailable")
        self.assertEqual(completed["result"]["error"], "relay unavailable")

    def test_suspended_generator_is_not_reported_as_ready(self) -> None:
        store = LifecycleJobStore()
        submitted = store.submit(
            "generator_start",
            "generator:suspended",
            lambda progress, cancelled: {
                "status": "suspended",
                "message": "Provider advertisement expired.",
            },
        )

        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["stage"], "failed")
        self.assertEqual(completed["error"], "Provider advertisement expired.")

    def test_closed_admission_returns_pollable_cancelled_job_without_starting(self) -> None:
        store = LifecycleJobStore()
        target_called = threading.Event()
        store.close_admission()

        submitted = store.submit(
            "node_start",
            "node:rejected",
            lambda progress, cancelled: target_called.set() or {"status": "ready"},
        )

        self.assertFalse(target_called.is_set())
        self.assertEqual(submitted["status"], "cancelled")
        self.assertEqual(submitted["stage"], "admission_closed")
        self.assertTrue(submitted["cancel_requested"])
        stored = store.get(submitted["job_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["job_id"], submitted["job_id"])
        self.assertEqual(stored["stage"], "admission_closed")
        self.assertEqual(
            store.diagnostics(),
            {
                "admission_open": False,
                "active_thread_count": 0,
                "active_job_ids": [],
            },
        )

    def test_cancel_all_and_wait_closes_admission_and_joins_workers(self) -> None:
        store = LifecycleJobStore()
        target_finished = threading.Event()

        def target(progress, cancelled):
            progress("loading", "Waiting for shutdown.")
            self.assertTrue(cancelled.wait(1))
            target_finished.set()
            return {"status": "cancelled"}

        submitted = store.submit("node_start", "node:shutdown", target)

        self.assertTrue(store.cancel_all_and_wait(1))
        self.assertTrue(target_finished.is_set())
        self.assertFalse(store.admission_open)
        self.assertEqual(store.diagnostics()["active_thread_count"], 0)
        self.assertEqual(store.get(submitted["job_id"])["status"], "cancelled")

    def test_reopen_is_rejected_until_active_workers_have_stopped(self) -> None:
        store = LifecycleJobStore()
        release = threading.Event()

        def target(progress, cancelled):
            release.wait(1)
            return {"status": "cancelled"}

        store.submit("node_start", "node:still-active", target)
        self.assertFalse(store.cancel_all_and_wait(0))

        with self.assertRaisesRegex(RuntimeError, "workers are active"):
            store.reopen_admission()

        release.set()
        self.assertTrue(store.cancel_all_and_wait(1))
        store.reopen_admission()
        self.assertTrue(store.admission_open)
        self.assertTrue(store.cancel_all_and_wait(0))

    def test_cancel_all_and_wait_has_one_shared_bounded_deadline(self) -> None:
        store = LifecycleJobStore()
        release = threading.Event()

        def target(progress, cancelled):
            progress("loading", "Ignoring cancellation until released.")
            release.wait(1)
            return {"status": "cancelled"}

        submitted = store.submit("node_start", "node:slow-shutdown", target)
        started_at = time.monotonic()

        self.assertFalse(store.cancel_all_and_wait(0.02))
        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertEqual(store.diagnostics()["active_thread_count"], 1)

        release.set()
        self.assertTrue(store.cancel_all_and_wait(1))
        self.assertEqual(store.get(submitted["job_id"])["status"], "cancelled")

    def test_reopen_admission_allows_a_new_job_after_shutdown(self) -> None:
        store = LifecycleJobStore()
        self.assertTrue(store.cancel_all_and_wait(0))
        rejected = store.submit(
            "generator_start",
            "generator:closed",
            lambda progress, cancelled: {"status": "ready"},
        )
        self.assertEqual(rejected["stage"], "admission_closed")

        store.reopen_admission()
        submitted = store.submit(
            "generator_start",
            "generator:reopened",
            lambda progress, cancelled: {"status": "ready"},
        )
        completed = wait_for_terminal(store, submitted["job_id"])

        self.assertTrue(store.admission_open)
        self.assertEqual(completed["status"], "ready")
        self.assertEqual(store.diagnostics()["active_thread_count"], 0)


class LifecycleEndpointTests(unittest.TestCase):
    def test_generator_start_async_returns_before_slow_start_finishes(self) -> None:
        from api import server as api_server

        original_store = api_server._lifecycle_jobs
        original_start = api_server._start_generator_runtime
        api_server._lifecycle_jobs = LifecycleJobStore()

        async def slow_start(req, *, external_cancel=None):
            await asyncio.sleep(0.15)
            return {"status": "ready"}

        api_server._start_generator_runtime = slow_start
        request = api_server.GeneratorStartRequest(model_name="facebook/opt-125m")
        try:
            started_at = time.perf_counter()
            submitted = asyncio.run(api_server.start_generator_async(request))
            elapsed = time.perf_counter() - started_at
            completed = wait_for_terminal(api_server._lifecycle_jobs, submitted["job_id"])
        finally:
            api_server._start_generator_runtime = original_start
            api_server._lifecycle_jobs = original_store

        self.assertLess(elapsed, 0.1)
        self.assertEqual(completed["status"], "ready")


if __name__ == "__main__":
    unittest.main()
