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


class LifecycleEndpointTests(unittest.TestCase):
    def test_generator_start_async_returns_before_slow_start_finishes(self) -> None:
        from api import server as api_server

        original_store = api_server._lifecycle_jobs
        original_start = api_server.start_generator
        api_server._lifecycle_jobs = LifecycleJobStore()

        async def slow_start(req):
            await asyncio.sleep(0.15)
            return {"status": "ready"}

        api_server.start_generator = slow_start
        request = api_server.GeneratorStartRequest(model_name="facebook/opt-125m")
        try:
            started_at = time.perf_counter()
            submitted = asyncio.run(api_server.start_generator_async(request))
            elapsed = time.perf_counter() - started_at
            completed = wait_for_terminal(api_server._lifecycle_jobs, submitted["job_id"])
        finally:
            api_server.start_generator = original_start
            api_server._lifecycle_jobs = original_store

        self.assertLess(elapsed, 0.1)
        self.assertEqual(completed["status"], "ready")


if __name__ == "__main__":
    unittest.main()
