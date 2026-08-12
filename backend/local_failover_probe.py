"""Run real local Hivemind expert failover with two complete replicas."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import hivemind
import torch
from huggingface_hub import snapshot_download

from client.failover import RouteFailoverConfig
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from constants import P2PNetworkConfig, SUPPORTED_MODELS
from local_split_probe import _write_private_json
from node.node import Node
from node.rpc_server import _run_with_timeout

SCHEMA_VERSION = 1
DEFAULT_MODEL = "facebook/opt-125m"


class LocalFailoverProbeError(RuntimeError):
    """Raised when process-level expert failover cannot be proven."""


@dataclass(frozen=True)
class ProbeOptions:
    model_name: str = DEFAULT_MODEL
    sequence_length: int = 4
    route_timeout: float = 60.0
    output: Path | None = None
    allow_download: bool = False


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _validate_options(options: ProbeOptions) -> tuple[int, int]:
    model_name = options.model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise LocalFailoverProbeError(f"Unsupported model: {model_name}")
    if options.sequence_length <= 0:
        raise LocalFailoverProbeError("sequence_length must be positive")
    if options.route_timeout <= 0:
        raise LocalFailoverProbeError("route_timeout must be positive")
    model = SUPPORTED_MODELS[model_name]
    return int(model["num_layers"]), int(model["hidden_size"])


def _local_snapshot(model_name: str, allow_download: bool) -> str:
    try:
        return snapshot_download(
            repo_id=model_name,
            local_files_only=not allow_download,
            token=False,
        )
    except Exception as exc:
        action = "download or cache" if allow_download else "find in the local cache"
        raise LocalFailoverProbeError(
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
        raise LocalFailoverProbeError("Local bootstrap exposed no visible addresses")
    return addresses


def _wait_for_replicas(
    sequential: RemoteSequential,
    timeout: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            nodes = sequential._discover_nodes()
            plan = sequential._build_route_plan(nodes)
            if plan["active"] is not None and plan["alternates"]:
                return nodes
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise LocalFailoverProbeError(
        "Two complete local replicas did not become reachable within "
        f"{timeout:g} seconds: {last_error}"
    )


def _node_evidence(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "peer_id": str(node.get("peer_id", "")),
        "rpc_uid": str(node.get("rpc_uid", "")),
        "layer_start": int(node["layer_start"]),
        "layer_end": int(node["layer_end"]),
    }


def _accounting_evidence(node: Node) -> dict[str, Any]:
    snapshot = node.get_accounting_snapshot()
    return {
        "peer_id": node.get_peer_id(),
        "requests_served": int(snapshot.get("requests_served") or 0),
        "failed_requests": int(snapshot.get("failed_requests") or 0),
        "token_positions_served": int(snapshot.get("token_positions_served") or 0),
    }


def run_probe(options: ProbeOptions) -> dict[str, Any]:
    total_layers, hidden_size = _validate_options(options)
    model_name = options.model_name.strip()
    model_path = _local_snapshot(model_name, options.allow_download)
    prefix = f"distribllm-local-failover-{os.getpid()}-{int(time.time())}"
    started_at = time.perf_counter()

    bootstrap: hivemind.DHT | None = None
    client_dht: hivemind.DHT | None = None
    nodes: list[Node] = []
    sequential: RemoteSequential | None = None
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
        for suffix in (0, 1):
            node = Node(
                model_name=model_name,
                layer_start=0,
                layer_end=total_layers,
                dht_prefix=prefix,
                initial_peers=initial_peers,
                device="cpu",
                dtype=torch.float32,
                local_model_path=model_path,
                rpc_uid_suffix=suffix,
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
            failover_config=RouteFailoverConfig(
                max_attempts=2,
                max_alternates=1,
                backoff_seconds=0,
                quarantine_seconds=30,
            ),
        )
        discovered = _wait_for_replicas(sequential, options.route_timeout)
        sequential.start_session(f"local-failover-{os.getpid()}")

        torch.manual_seed(0)
        hidden_states = torch.randn(
            1,
            options.sequence_length,
            hidden_size,
            dtype=torch.float32,
        )
        attention_mask = torch.ones(
            (1, options.sequence_length),
            dtype=torch.bool,
        )
        position_ids = torch.arange(options.sequence_length).unsqueeze(0)
        first_output, first_trace = sequential.forward(
            hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        first_metrics = sequential.get_last_forward_metrics()
        active_route = first_metrics.get("active_route") or []
        if len(active_route) != 1:
            raise LocalFailoverProbeError(
                f"Expected one full-range active provider, got {len(active_route)}"
            )
        failed_peer_id = str(active_route[0]["peer_id"])
        node_by_peer = {str(node.get_peer_id()): node for node in nodes}
        failed_node = node_by_peer.get(failed_peer_id)
        if failed_node is None:
            raise LocalFailoverProbeError("Selected provider did not map to a local node")
        failed_node.turn_off(timeout=10)

        second_output, second_trace = sequential.forward(
            hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        second_metrics = sequential.get_last_forward_metrics()
        accepted_route = second_metrics.get("active_route") or []
        accepted_peer_id = (
            str(accepted_route[0].get("peer_id", "")) if accepted_route else ""
        )
        accounting = [_accounting_evidence(node) for node in nodes]
        output_match = torch.allclose(
            first_output,
            second_output,
            atol=0.02,
            rtol=0.02,
        )
        ok = bool(
            len(discovered) == 2
            and len(first_trace) == 1
            and len(second_trace) == 1
            and second_metrics.get("failed_over") is True
            and int(second_metrics.get("attempt_count") or 0) == 2
            and accepted_peer_id
            and accepted_peer_id != failed_peer_id
            and output_match
            and all(item["failed_requests"] == 0 for item in accounting)
            and all(item["requests_served"] == 1 for item in accounting)
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "runtime": {
                "python_version": platform.python_version(),
                "hivemind_version": _version("hivemind"),
                "torch_version": _version("torch"),
                "platform": platform.system(),
                "machine": platform.machine(),
            },
            "model_name": model_name,
            "total_layers": total_layers,
            "initial_route": [_node_evidence(node) for node in active_route],
            "failed_peer_id": failed_peer_id,
            "accepted_route": [_node_evidence(node) for node in accepted_route],
            "first_trace": first_trace,
            "second_trace": second_trace,
            "failover": {
                "attempt_count": second_metrics.get("attempt_count"),
                "failed_over": second_metrics.get("failed_over"),
                "reasons": second_metrics.get("failover_reasons", []),
                "route_revision": second_metrics.get("route_revision"),
            },
            "output_match": output_match,
            "max_abs_diff": float((first_output - second_output).abs().max().item()),
            "accounting": accounting,
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        failure = exc
    finally:
        if sequential is not None:
            sequential.end_session()
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
                "local-failover-client-dht-shutdown", client_dht.shutdown, 10
            )
        if bootstrap is not None:
            cleanup["bootstrap_stopped"] = _run_with_timeout(
                "local-failover-bootstrap-shutdown", bootstrap.shutdown, 10
            )

    if failure is not None:
        raise LocalFailoverProbeError(str(failure)) from failure
    if result is None:
        raise LocalFailoverProbeError("Local failover probe produced no result")
    result["cleanup"] = cleanup
    result["ok"] = bool(
        result["ok"]
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
    parser.add_argument("--sequence-length", type=int, default=4)
    parser.add_argument("--route-timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-download", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = ProbeOptions(
        model_name=args.model,
        sequence_length=args.sequence_length,
        route_timeout=args.route_timeout,
        output=args.output,
        allow_download=args.allow_download,
    )
    try:
        result = run_probe(options)
    except LocalFailoverProbeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
