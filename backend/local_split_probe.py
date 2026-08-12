"""Run real local multi-peer split inference and parity acceptance."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import hivemind
import torch
from huggingface_hub import snapshot_download

from client.generation import DistributedGenerator
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from constants import P2PNetworkConfig, SUPPORTED_MODELS
from node.node import Node
from node.rpc_server import _run_with_timeout

SCHEMA_VERSION = 1
DEFAULT_MODEL = "facebook/opt-125m"
DEFAULT_PROMPT = "The capital of France is"


class LocalSplitProbeError(RuntimeError):
    """Raised when the local split acceptance probe cannot complete."""


@dataclass(frozen=True)
class ProbeOptions:
    model_name: str = DEFAULT_MODEL
    split_layer: int = 6
    prompt: str = DEFAULT_PROMPT
    max_new_tokens: int = 2
    route_timeout: float = 60.0
    output: Path | None = None
    allow_download: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _validate_options(options: ProbeOptions) -> int:
    model_name = options.model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise LocalSplitProbeError(f"Unsupported model: {model_name}")
    total_layers = int(SUPPORTED_MODELS[model_name]["num_layers"])
    if not 0 < options.split_layer < total_layers:
        raise LocalSplitProbeError(
            f"split_layer must be between 1 and {total_layers - 1}"
        )
    if not options.prompt.strip():
        raise LocalSplitProbeError("prompt must not be empty")
    if options.max_new_tokens <= 0:
        raise LocalSplitProbeError("max_new_tokens must be positive")
    if options.route_timeout <= 0:
        raise LocalSplitProbeError("route_timeout must be positive")
    return total_layers


def _local_snapshot(model_name: str, allow_download: bool) -> str:
    try:
        return snapshot_download(
            repo_id=model_name,
            local_files_only=not allow_download,
            token=False,
        )
    except Exception as exc:
        action = "download or cache" if allow_download else "find in the local cache"
        raise LocalSplitProbeError(
            f"Could not {action} {model_name}. Rerun with --allow-download if needed: {exc}"
        ) from exc


def _direct_config() -> P2PNetworkConfig:
    return P2PNetworkConfig(
        mode="direct",
        port=0,
        announce_maddrs=(),
        trusted_relays=(),
        auto_nat=False,
        nat_port_map=False,
        use_auto_relay=False,
        relay_wait_timeout=0,
    )


def _bootstrap_addresses(dht: hivemind.DHT) -> list[str]:
    addresses = [str(address) for address in dht.get_visible_maddrs()]
    if not addresses:
        raise LocalSplitProbeError("Local bootstrap exposed no visible addresses")
    return addresses


def _wait_for_route(
    sequential: RemoteSequential,
    timeout: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return sequential.validate_reachable_route()
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise LocalSplitProbeError(
        f"Complete local split route did not become reachable within {timeout:g} "
        f"seconds: {last_error}"
    )


def _route_evidence(route: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "peer_id": str(node.get("peer_id", "")),
            "rpc_uid": str(node.get("rpc_uid", "")),
            "layer_start": int(node["layer_start"]),
            "layer_end": int(node["layer_end"]),
            "model_name": str(node.get("model_name", "")),
        }
        for node in route
    ]


def _accounting_evidence(node: Node) -> dict[str, Any]:
    snapshot = node.get_accounting_snapshot()
    return {
        field: snapshot.get(field)
        for field in (
            "peer_id",
            "model_name",
            "layer_start",
            "layer_end",
            "requests_served",
            "failed_requests",
            "token_positions_served",
            "total_latency_ms",
            "last_success_at",
            "last_error_at",
        )
    }


def _write_private_json(path: Path, document: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def run_probe(options: ProbeOptions) -> dict[str, Any]:
    total_layers = _validate_options(options)
    model_name = options.model_name.strip()
    model_path = _local_snapshot(model_name, options.allow_download)
    prefix = f"distribllm-local-split-{os.getpid()}-{int(time.time())}"
    started_at = time.perf_counter()

    bootstrap: hivemind.DHT | None = None
    client_dht: hivemind.DHT | None = None
    nodes: list[Node] = []
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
        ranges = [(0, options.split_layer), (options.split_layer, total_layers)]
        for start, end in ranges:
            node = Node(
                model_name=model_name,
                layer_start=start,
                layer_end=end,
                dht_prefix=prefix,
                initial_peers=initial_peers,
                device="cpu",
                dtype=torch.float32,
                local_model_path=model_path,
                p2p_config=_direct_config(),
            )
            nodes.append(node)
            node.start()

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
            model_name=model_name,
        )
        route = _wait_for_route(sequential, options.route_timeout)
        generator = DistributedGenerator(
            model_name=model_name,
            sequential=sequential,
            device="cpu",
            dtype=torch.float32,
            local_model_path=model_path,
        )
        generator.load()
        cold_start = generator.get_performance_snapshot()

        parity = generator.compare_next_token_logits(
            prompt=options.prompt,
            atol=0.02,
            rtol=0.02,
        )
        generation = asyncio.run(
            generator.compare_generated_output(
                prompt=options.prompt,
                max_new_tokens=options.max_new_tokens,
                do_sample=False,
            )
        )
        generator.unload()
        warm_generator = DistributedGenerator(
            model_name=model_name,
            sequential=sequential,
            device="cpu",
            dtype=torch.float32,
            local_model_path=model_path,
        )
        warm_generator.load()
        warm_start = warm_generator.get_performance_snapshot()
        generator = warm_generator

        async def cancellation_baseline() -> dict[str, Any]:
            started_at = time.perf_counter()

            async def consume() -> list[dict[str, Any]]:
                return [
                    chunk
                    async for chunk in generator.generate_stream(
                        prompt=options.prompt,
                        max_new_tokens=64,
                        do_sample=False,
                    )
                ]

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)
            requested_at = time.perf_counter()
            generator.request_stop()
            chunks = await asyncio.wait_for(task, timeout=5)
            completed = next(
                (chunk for chunk in chunks if chunk.get("done") is True),
                {},
            )
            return {
                "request_to_completion_ms": (
                    time.perf_counter() - requested_at
                ) * 1000,
                "total_ms": (time.perf_counter() - started_at) * 1000,
                "stopped": bool((completed.get("metrics") or {}).get("stopped")),
                "generated_tokens": int(
                    (completed.get("metrics") or {}).get("generated_tokens", 0)
                ),
            }

        cancellation = asyncio.run(cancellation_baseline())
        route_evidence = _route_evidence(route)
        accounting = [_accounting_evidence(node) for node in nodes]
        expected_ranges = [(0, options.split_layer), (options.split_layer, total_layers)]
        actual_ranges = [
            (item["layer_start"], item["layer_end"]) for item in route_evidence
        ]
        distinct_peers = {item["peer_id"] for item in route_evidence}
        accounting_ok = all(
            int(item.get("requests_served") or 0) > 0
            and int(item.get("token_positions_served") or 0) > 0
            and int(item.get("failed_requests") or 0) == 0
            for item in accounting
        )
        ok = bool(
            actual_ranges == expected_ranges
            and len(distinct_peers) == 2
            and parity.get("argmax_match") is True
            and parity.get("allclose") is True
            and generation.get("exact_text_match") is True
            and len(generation.get("node_trace") or []) == 2
            and accounting_ok
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": _utc_now(),
            "ok": ok,
            "runtime": {
                "python_version": platform.python_version(),
                "hivemind_version": _version("hivemind"),
                "torch_version": _version("torch"),
                "transformers_version": _version("transformers"),
                "platform": platform.system(),
                "machine": platform.machine(),
            },
            "model_name": model_name,
            "total_layers": total_layers,
            "expected_ranges": [
                {"layer_start": start, "layer_end": end}
                for start, end in expected_ranges
            ],
            "route": route_evidence,
            "parity": {
                key: parity.get(key)
                for key in (
                    "argmax_match",
                    "allclose",
                    "atol",
                    "rtol",
                    "max_abs_diff",
                    "mean_abs_diff",
                    "direct_next_token_id",
                    "distributed_next_token_id",
                    "node_trace",
                )
            },
            "generation": {
                key: generation.get(key)
                for key in (
                    "exact_text_match",
                    "direct_response",
                    "distributed_response",
                    "direct_generated_token_ids",
                    "node_trace",
                    "performance",
                )
            },
            "performance": {
                "cold_start": cold_start,
                "warm_start": warm_start,
                "cancellation": cancellation,
            },
            "accounting": accounting,
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        failure = exc
    finally:
        if generator is not None:
            try:
                generator.unload()
                cleanup["generator_unloaded"] = True
            except Exception as exc:
                cleanup["generator_unloaded"] = False
                cleanup["generator_error"] = str(exc)
        node_results = []
        for node in reversed(nodes):
            try:
                node.stop(timeout=10)
                node_results.append({"node_id": node.node_id, "stopped": True})
            except Exception as exc:
                node_results.append(
                    {"node_id": node.node_id, "stopped": False, "error": str(exc)}
                )
        cleanup["nodes"] = list(reversed(node_results))
        if client_dht is not None:
            cleanup["remote_expert_p2p_stopped"] = shutdown_remote_expert_p2p(
                client_dht
            )
            cleanup["client_dht_stopped"] = _run_with_timeout(
                "local-split-client-dht-shutdown", client_dht.shutdown, 10
            )
        if bootstrap is not None:
            cleanup["bootstrap_stopped"] = _run_with_timeout(
                "local-split-bootstrap-shutdown", bootstrap.shutdown, 10
            )

    if failure is not None:
        raise LocalSplitProbeError(str(failure)) from failure
    if result is None:
        raise LocalSplitProbeError("Local split probe produced no result")
    result["cleanup"] = cleanup
    result["ok"] = bool(
        result["ok"]
        and cleanup.get("generator_unloaded") is True
        and cleanup.get("client_dht_stopped") is True
        and cleanup.get("remote_expert_p2p_stopped") is True
        and cleanup.get("bootstrap_stopped") is True
        and all(item.get("stopped") is True for item in cleanup["nodes"])
    )
    if options.output is not None:
        _write_private_json(options.output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--split-layer", type=int, default=6)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-new-tokens", type=int, default=2)
    parser.add_argument("--route-timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-download", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = ProbeOptions(
        model_name=args.model,
        split_layer=args.split_layer,
        prompt=args.prompt,
        max_new_tokens=args.max_new_tokens,
        route_timeout=args.route_timeout,
        output=args.output,
        allow_download=args.allow_download,
    )
    try:
        result = run_probe(options)
    except LocalSplitProbeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
