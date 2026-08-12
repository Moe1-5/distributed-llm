"""Run a bounded real TinyLlama distributed performance baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import hivemind
import psutil
import torch

from client.generation import DistributedGenerator
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from constants import SUPPORTED_MODELS
from local_split_probe import (
    _bootstrap_addresses,
    _direct_config,
    _local_snapshot,
    _utc_now,
    _version,
    _wait_for_route,
    _write_private_json,
)
from node.node import Node
from node.rpc_server import _run_with_timeout

SCHEMA_VERSION = 1
MODEL_NAME = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
DEFAULT_PROMPT = "What is the capital of France?"


class TinyLlamaProbeError(RuntimeError):
    """Raised when the TinyLlama baseline cannot complete."""


@dataclass(frozen=True)
class ProbeOptions:
    prompt: str = DEFAULT_PROMPT
    max_new_tokens: int = 2
    route_timeout: float = 90.0
    output: Path | None = None
    allow_download: bool = False


def _validate_options(options: ProbeOptions) -> int:
    if MODEL_NAME not in SUPPORTED_MODELS:
        raise TinyLlamaProbeError(f"Unsupported model: {MODEL_NAME}")
    if not options.prompt.strip():
        raise TinyLlamaProbeError("prompt must not be empty")
    if options.max_new_tokens <= 0:
        raise TinyLlamaProbeError("max_new_tokens must be positive")
    if options.route_timeout <= 0:
        raise TinyLlamaProbeError("route_timeout must be positive")
    return int(SUPPORTED_MODELS[MODEL_NAME]["num_layers"])


def _bytes_to_gib(value: int | float) -> float:
    return float(value) / (1024**3)


def _process_tree_rss() -> int:
    process = psutil.Process()
    rss = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            rss += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rss


class _ResourceSampler:
    def __init__(self, interval: float = 0.1) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._minimum_available = psutil.virtual_memory().available
        self._maximum_tree_rss = _process_tree_rss()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._sample_loop,
            daemon=True,
            name="tinyllama-resource-sampler",
        )
        self._thread.start()

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval):
            available = psutil.virtual_memory().available
            tree_rss = _process_tree_rss()
            with self._lock:
                self._minimum_available = min(self._minimum_available, available)
                self._maximum_tree_rss = max(self._maximum_tree_rss, tree_rss)

    def stop(self) -> dict[str, float]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 5))
        with self._lock:
            return {
                "minimum_system_available_ram_gib": _bytes_to_gib(
                    self._minimum_available
                ),
                "maximum_process_tree_rss_gib": _bytes_to_gib(
                    self._maximum_tree_rss
                ),
            }


async def _generate(
    generator: DistributedGenerator,
    options: ProbeOptions,
) -> dict[str, Any]:
    response = ""
    node_trace: list[str] = []
    metrics: dict[str, Any] | None = None
    async for chunk in generator.generate_stream(
        prompt=options.prompt,
        max_new_tokens=options.max_new_tokens,
        do_sample=False,
    ):
        if "token" in chunk:
            response += str(chunk["token"])
        elif "done" in chunk:
            node_trace = list(chunk.get("node_trace") or [])
            raw_metrics = chunk.get("metrics")
            metrics = dict(raw_metrics) if isinstance(raw_metrics, dict) else None
        elif "error" in chunk:
            raise TinyLlamaProbeError(str(chunk["error"]))
    if metrics is None:
        raise TinyLlamaProbeError("Generation completed without performance metrics")
    return {"response": response, "node_trace": node_trace, "metrics": metrics}


def _performance_evidence(source: dict[str, Any]) -> dict[str, Any]:
    result = {
        field: source.get(field)
        for field in (
            "time_to_first_token_ms",
            "total_duration_ms",
            "generated_tokens",
            "tokens_per_second",
            "route_validation_ms_total",
            "stopped",
        )
    }
    raw_hops = source.get("hop_metrics")
    result["hop_metrics"] = [
        {
            field: hop.get(field)
            for field in (
                "peer_id",
                "rpc_uid",
                "layer_start",
                "layer_end",
                "calls",
                "total_latency_ms",
                "average_latency_ms",
                "last_latency_ms",
            )
        }
        for hop in (raw_hops if isinstance(raw_hops, list) else [])
        if isinstance(hop, dict)
    ]
    return result


def _baseline_ok(
    route: Sequence[dict[str, Any]],
    generation: dict[str, Any],
    accounting: dict[str, Any],
    total_layers: int,
) -> bool:
    metrics = generation.get("metrics")
    hops = metrics.get("hop_metrics") if isinstance(metrics, dict) else None
    return bool(
        len(route) == 1
        and int(route[0].get("layer_start", -1)) == 0
        and int(route[0].get("layer_end", -1)) == total_layers
        and generation.get("response")
        and len(generation.get("node_trace") or []) == 1
        and isinstance(metrics, dict)
        and metrics.get("time_to_first_token_ms") is not None
        and float(metrics.get("total_duration_ms") or 0) > 0
        and int(metrics.get("generated_tokens") or 0) > 0
        and float(metrics.get("tokens_per_second") or 0) > 0
        and isinstance(hops, list)
        and len(hops) == 1
        and int(hops[0].get("calls") or 0) > 0
        and int(accounting.get("requests_served") or 0) > 0
        and int(accounting.get("token_positions_served") or 0) > 0
        and int(accounting.get("failed_requests") or 0) == 0
    )


def run_probe(options: ProbeOptions) -> dict[str, Any]:
    total_layers = _validate_options(options)
    try:
        model_path = _local_snapshot(MODEL_NAME, options.allow_download)
    except Exception as exc:
        raise TinyLlamaProbeError(str(exc)) from exc

    prefix = f"distribllm-tinyllama-perf-{os.getpid()}-{int(time.time())}"
    sampler = _ResourceSampler()
    sampler.start()
    overall_started = time.perf_counter()
    bootstrap: hivemind.DHT | None = None
    client_dht: hivemind.DHT | None = None
    node: Node | None = None
    generator: DistributedGenerator | None = None
    cleanup: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    failure: Exception | None = None

    try:
        bootstrap = hivemind.DHT(
            start=True,
            use_ipfs=False,
            use_relay=False,
            host_maddrs=["/ip4/127.0.0.1/tcp/0"],
        )
        initial_peers = _bootstrap_addresses(bootstrap)

        node = Node(
            model_name=MODEL_NAME,
            layer_start=0,
            layer_end=total_layers,
            dht_prefix=prefix,
            initial_peers=initial_peers,
            device="cpu",
            dtype=torch.bfloat16,
            local_model_path=model_path,
            p2p_config=_direct_config(),
        )
        node_started = time.perf_counter()
        node.start()
        node_start_ms = (time.perf_counter() - node_started) * 1000

        client_dht = hivemind.DHT(
            start=True,
            use_ipfs=False,
            use_relay=False,
            client_mode=True,
            initial_peers=initial_peers,
            host_maddrs=["/ip4/127.0.0.1/tcp/0"],
        )
        sequential = RemoteSequential(
            dht=client_dht,
            dht_prefix=prefix,
            num_layers=total_layers,
            model_name=MODEL_NAME,
        )
        route_started = time.perf_counter()
        route = _wait_for_route(sequential, options.route_timeout)
        initial_route_validation_ms = (time.perf_counter() - route_started) * 1000

        generator = DistributedGenerator(
            model_name=MODEL_NAME,
            sequential=sequential,
            device="cpu",
            dtype=torch.bfloat16,
            local_model_path=model_path,
        )
        generator_started = time.perf_counter()
        generator.load()
        generator_load_ms = (time.perf_counter() - generator_started) * 1000
        generation = asyncio.run(_generate(generator, options))
        accounting = node.get_accounting_snapshot()
        performance = _performance_evidence(generation["metrics"])

        result = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": _utc_now(),
            "ok": _baseline_ok(route, generation, accounting, total_layers),
            "runtime": {
                "python_version": platform.python_version(),
                "hivemind_version": _version("hivemind"),
                "torch_version": _version("torch"),
                "transformers_version": _version("transformers"),
                "platform": platform.system(),
                "machine": platform.machine(),
                "device": "cpu",
                "dtype": "bfloat16",
            },
            "model_name": MODEL_NAME,
            "total_layers": total_layers,
            "route": [
                {
                    "peer_id": str(item.get("peer_id", "")),
                    "rpc_uid": str(item.get("rpc_uid", "")),
                    "layer_start": int(item["layer_start"]),
                    "layer_end": int(item["layer_end"]),
                }
                for item in route
            ],
            "startup": {
                "node_start_ms": node_start_ms,
                "initial_route_validation_ms": initial_route_validation_ms,
                "generator_load_ms": generator_load_ms,
            },
            "generation": {
                "response": generation["response"],
                "node_trace": generation["node_trace"],
                "performance": performance,
            },
            "accounting": {
                field: accounting.get(field)
                for field in (
                    "peer_id",
                    "layer_start",
                    "layer_end",
                    "requests_served",
                    "failed_requests",
                    "token_positions_served",
                    "total_latency_ms",
                )
            },
            "elapsed_seconds_before_cleanup": time.perf_counter() - overall_started,
        }
    except Exception as exc:
        failure = exc
    finally:
        resource_usage = sampler.stop()
        if generator is not None:
            try:
                generator.unload()
                cleanup["generator_unloaded"] = True
            except Exception as exc:
                cleanup["generator_unloaded"] = False
                cleanup["generator_error"] = str(exc)
        if node is not None:
            try:
                node.stop(timeout=15)
                cleanup["node_stopped"] = True
            except Exception as exc:
                cleanup["node_stopped"] = False
                cleanup["node_error"] = str(exc)
        if client_dht is not None:
            cleanup["remote_expert_p2p_stopped"] = shutdown_remote_expert_p2p(
                client_dht
            )
            cleanup["client_dht_stopped"] = _run_with_timeout(
                "tinyllama-client-dht-shutdown", client_dht.shutdown, 15
            )
        if bootstrap is not None:
            cleanup["bootstrap_stopped"] = _run_with_timeout(
                "tinyllama-bootstrap-shutdown", bootstrap.shutdown, 15
            )

    if failure is not None:
        raise TinyLlamaProbeError(str(failure)) from failure
    if result is None:
        raise TinyLlamaProbeError("TinyLlama probe produced no result")
    result["resources"] = resource_usage
    result["cleanup"] = cleanup
    result["elapsed_seconds"] = time.perf_counter() - overall_started
    result["ok"] = bool(
        result["ok"]
        and cleanup.get("generator_unloaded") is True
        and cleanup.get("node_stopped") is True
        and cleanup.get("client_dht_stopped") is True
        and cleanup.get("remote_expert_p2p_stopped") is True
        and cleanup.get("bootstrap_stopped") is True
    )
    if options.output is not None:
        _write_private_json(options.output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-new-tokens", type=int, default=2)
    parser.add_argument("--route-timeout", type=float, default=90)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-download", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_probe(
            ProbeOptions(
                prompt=args.prompt,
                max_new_tokens=args.max_new_tokens,
                route_timeout=args.route_timeout,
                output=args.output,
                allow_download=args.allow_download,
            )
        )
    except TinyLlamaProbeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
