"""Sweep deterministic legacy expert payloads against one physical worker.

This diagnostic intentionally bypasses tokenization and chat sampling. It
connects as a dialing-only Hivemind client, validates one exact full-model
provider, then sends bounded legacy tensor forwards until the first failure.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

import hivemind
import torch
from hivemind.compression import serialize_torch_tensor
from hivemind.proto.runtime_pb2 import CompressionType

from api.env_loader import load_project_env
from client.failover import RouteAttemptError
from client.rpc_policy import RPCAttemptPolicy, classify_rpc_error
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from constants import DHT_PREFIX, SUPPORTED_MODELS, get_initial_peers
from incentives.config import IncentivesConfig
from incentives.runtime import UsefulWorkRuntime
from local_split_probe import _write_private_json
from node.rpc_server import RPCServer, _run_with_timeout

SCHEMA_VERSION = 1
DEFAULT_MODEL = "facebook/opt-125m"
DEFAULT_SEQUENCE_LENGTHS = (1, 1, 2, 4, 8, 16, 32, 64, 96, 128)
MAX_PROBE_SEQUENCE_LENGTH = 2048
VALID_TRANSPORTS = frozenset({"direct", "relay"})


class TensorPayloadProbeError(RuntimeError):
    """Raised when the requested probe cannot safely target one exact worker."""


@dataclass(frozen=True)
class ProbeOptions:
    initial_peers: tuple[str, ...]
    expected_peer: str
    expected_transport: str
    model_name: str = DEFAULT_MODEL
    dht_prefix: str = DHT_PREFIX
    sequence_lengths: tuple[int, ...] = DEFAULT_SEQUENCE_LENGTHS
    route_timeout: float = 60.0
    expected_rpc_uid: str | None = None
    output: Path | None = None


ForwardCall = Callable[..., torch.Tensor]
Checkpoint = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_options(options: ProbeOptions) -> tuple[int, int, str]:
    model_name = options.model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise TensorPayloadProbeError(f"Unsupported model: {model_name}")
    if not options.initial_peers or any(not peer.strip() for peer in options.initial_peers):
        raise TensorPayloadProbeError("At least one non-empty initial peer is required")
    if not options.expected_peer.strip():
        raise TensorPayloadProbeError("expected_peer must not be empty")
    if options.expected_transport not in VALID_TRANSPORTS:
        raise TensorPayloadProbeError(
            "expected_transport must be either 'direct' or 'relay'"
        )
    if not options.dht_prefix.strip():
        raise TensorPayloadProbeError("dht_prefix must not be empty")
    if not options.sequence_lengths:
        raise TensorPayloadProbeError("At least one sequence length is required")
    if any(length <= 0 for length in options.sequence_lengths):
        raise TensorPayloadProbeError("All sequence lengths must be positive")
    if any(length > MAX_PROBE_SEQUENCE_LENGTH for length in options.sequence_lengths):
        raise TensorPayloadProbeError(
            "Sequence lengths must not exceed "
            f"the bounded probe limit of {MAX_PROBE_SEQUENCE_LENGTH}"
        )
    if options.route_timeout <= 0:
        raise TensorPayloadProbeError("route_timeout must be positive")

    model = SUPPORTED_MODELS[model_name]
    total_layers = int(model["num_layers"])
    hidden_size = int(model["hidden_size"])
    expected_rpc_uid = (
        options.expected_rpc_uid.strip()
        if options.expected_rpc_uid is not None
        else RPCServer.build_rpc_uid(
            options.dht_prefix.strip(),
            0,
            total_layers,
            provider_peer_id=options.expected_peer.strip(),
        )
    )
    if not expected_rpc_uid:
        raise TensorPayloadProbeError("expected_rpc_uid must not be empty")
    return total_layers, hidden_size, expected_rpc_uid


def _route_evidence(node: dict[str, Any]) -> dict[str, Any]:
    """Keep route identity and transport proof without copying multiaddresses."""
    return {
        "peer_id": str(node.get("peer_id", "")),
        "rpc_peer_id": str(node.get("rpc_peer_id", node.get("peer_id", ""))),
        "rpc_uid": str(node.get("rpc_uid", "")),
        "rpc_uid_schema_version": int(node.get("rpc_uid_schema_version", 1)),
        "model_name": str(node.get("model_name", "")),
        "layer_start": int(node.get("layer_start", -1)),
        "layer_end": int(node.get("layer_end", -1)),
        "connection_mode": str(node.get("connection_mode", "unknown")),
        "transport_verified": node.get("transport_verified") is True,
    }


def _validate_route_identity(
    route: Sequence[dict[str, Any]],
    options: ProbeOptions,
    *,
    total_layers: int,
    expected_rpc_uid: str,
) -> dict[str, Any]:
    if len(route) != 1:
        raise TensorPayloadProbeError(
            f"Probe requires one full-model provider; selected route has {len(route)} hops"
        )
    node = dict(route[0])
    checks = {
        "peer_id": str(node.get("peer_id", "")) == options.expected_peer.strip(),
        "model_name": str(node.get("model_name", "")) == options.model_name.strip(),
        "layer_range": (
            int(node.get("layer_start", -1)) == 0
            and int(node.get("layer_end", -1)) == total_layers
        ),
        "rpc_uid": str(node.get("rpc_uid", "")) == expected_rpc_uid,
        "connection_mode": (
            str(node.get("connection_mode", "")) == options.expected_transport
        ),
        "transport_verified": node.get("transport_verified") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise TensorPayloadProbeError(
            "Selected provider does not match the controlled target: "
            + ", ".join(failed)
        )
    return node


def _serialized_bytes(tensor: torch.Tensor, compression: CompressionType) -> int:
    return int(serialize_torch_tensor(tensor, compression).ByteSize())


def _request_measurements(
    hidden_states: torch.Tensor,
    attention_mask: torch.Tensor,
    position_ids: torch.Tensor,
) -> dict[str, int]:
    tensors = (hidden_states, attention_mask, position_ids)
    return {
        "logical_tensor_bytes": sum(
            tensor.numel() * tensor.element_size() for tensor in tensors
        ),
        "serialized_tensor_protobuf_bytes": (
            _serialized_bytes(hidden_states, CompressionType.FLOAT16)
            + _serialized_bytes(attention_mask, CompressionType.NONE)
            + _serialized_bytes(position_ids, CompressionType.NONE)
        ),
    }


def _failure_evidence(exc: Exception) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc),
        "failure_class": (
            exc.failure_class
            if isinstance(exc, RouteAttemptError)
            else classify_rpc_error(exc)
        ),
    }


def _run_payload_sweep(
    *,
    sequence_lengths: Sequence[int],
    hidden_size: int,
    node: dict[str, Any],
    forward_call: ForwardCall,
    checkpoint: Checkpoint | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    cases: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None

    for case_index, sequence_length in enumerate(sequence_lengths, start=1):
        request_id = str(uuid4())
        stage = "build_tensors"
        started_at = time.perf_counter()
        try:
            hidden_states = torch.linspace(
                -1.0,
                1.0,
                steps=sequence_length * hidden_size,
                dtype=torch.float32,
            ).reshape(1, sequence_length, hidden_size)
            attention_mask = torch.ones((1, sequence_length), dtype=torch.long)
            position_ids = torch.arange(sequence_length, dtype=torch.long).reshape(1, -1)

            stage = "serialize_request"
            measurements = _request_measurements(
                hidden_states,
                attention_mask,
                position_ids,
            )
            running_case: dict[str, Any] = {
                "case_index": case_index,
                "request_id": request_id,
                "sequence_length": sequence_length,
                "shape": list(hidden_states.shape),
                "rpc_mode": "legacy",
                "rpc_uid": str(node["rpc_uid"]),
                **measurements,
                "stage": "remote_forward",
                "status": "running",
            }
            cases.append(running_case)
            if checkpoint is not None:
                checkpoint({"stage": "remote_forward", "cases": list(cases)})

            stage = "remote_forward"
            with torch.inference_mode():
                output = forward_call(
                    rpc_uid=str(node["rpc_uid"]),
                    peer_id=str(node["peer_id"]),
                    hidden_states=hidden_states,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                    node_info=node,
                    receipt_route=None,
                    request_id=request_id,
                    hop_index=0,
                )

            stage = "validate_response"
            if not isinstance(output, torch.Tensor):
                raise TensorPayloadProbeError(
                    f"Expert returned {type(output).__name__}, expected torch.Tensor"
                )
            if tuple(output.shape) != tuple(hidden_states.shape):
                raise TensorPayloadProbeError(
                    "Expert returned shape "
                    f"{tuple(output.shape)}, expected {tuple(hidden_states.shape)}"
                )
            if not bool(torch.isfinite(output).all()):
                raise TensorPayloadProbeError("Expert returned non-finite values")

            stage = "serialize_response"
            completed_case = {
                **running_case,
                "response_logical_tensor_bytes": (
                    output.numel() * output.element_size()
                ),
                "response_serialized_tensor_protobuf_bytes": _serialized_bytes(
                    output,
                    CompressionType.FLOAT16,
                ),
                "response_dtype": str(output.dtype),
                "elapsed_ms": (time.perf_counter() - started_at) * 1000,
                "stage": "complete",
                "status": "passed",
            }
            cases[-1] = completed_case
            if checkpoint is not None:
                checkpoint({"stage": "between_cases", "cases": list(cases)})
        except Exception as exc:
            failed_case = {
                **(cases[-1] if cases and cases[-1].get("request_id") == request_id else {
                    "case_index": case_index,
                    "request_id": request_id,
                    "sequence_length": sequence_length,
                    "rpc_mode": "legacy",
                    "rpc_uid": str(node.get("rpc_uid", "")),
                }),
                "elapsed_ms": (time.perf_counter() - started_at) * 1000,
                "stage": stage,
                "status": "failed",
                **_failure_evidence(exc),
            }
            if cases and cases[-1].get("request_id") == request_id:
                cases[-1] = failed_case
            else:
                cases.append(failed_case)
            failure = {
                "case_index": case_index,
                "request_id": request_id,
                "sequence_length": sequence_length,
                "stage": stage,
                **_failure_evidence(exc),
            }
            if checkpoint is not None:
                checkpoint({"stage": stage, "cases": list(cases), "failure": failure})
            break

    return cases, failure


def _wait_for_controlled_route(
    sequential: RemoteSequential,
    options: ProbeOptions,
    *,
    total_layers: int,
    expected_rpc_uid: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + options.route_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            route = sequential.validate_reachable_route()
            return _validate_route_identity(
                route,
                options,
                total_layers=total_layers,
                expected_rpc_uid=expected_rpc_uid,
            )
        except TensorPayloadProbeError:
            raise
        except Exception as exc:
            last_error = exc
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise TensorPayloadProbeError(
        "Controlled provider did not become reachable within "
        f"{options.route_timeout:g} seconds: {last_error}"
    )


def run_probe(options: ProbeOptions) -> dict[str, Any]:
    total_layers, hidden_size, expected_rpc_uid = _validate_options(options)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe": "legacy_tensor_payload_sweep",
        "ok": False,
        "captured_at": _utc_now(),
        "stage": "start_dht",
        "runtime": {
            "python": platform.python_version(),
            "hivemind": hivemind.__version__,
            "torch": torch.__version__,
        },
        "configuration": {
            "model_name": options.model_name.strip(),
            "dht_prefix": options.dht_prefix.strip(),
            "total_layers": total_layers,
            "hidden_size": hidden_size,
            "sequence_lengths": list(options.sequence_lengths),
            "initial_peer_count": len(options.initial_peers),
            "expected_peer": options.expected_peer.strip(),
            "expected_rpc_uid": expected_rpc_uid,
            "expected_transport": options.expected_transport,
            "rpc_mode": "legacy",
            "attempts_per_case": 1,
            "stop_on_first_failure": True,
        },
        "route": None,
        "cases": [],
        "failure": None,
        "cleanup": {
            "remote_expert_p2p_stopped": None,
            "client_dht_stopped": None,
        },
    }

    def checkpoint(update: dict[str, Any]) -> None:
        result.update(update)
        if options.output is not None:
            _write_private_json(options.output, result)

    dht: hivemind.DHT | None = None
    measurement_ok = False
    try:
        checkpoint({"stage": "start_dht"})
        dht = hivemind.DHT(
            initial_peers=list(options.initial_peers),
            start=True,
            use_ipfs=False,
            use_relay=True,
            client_mode=True,
        )
        sequential = RemoteSequential(
            dht,
            options.dht_prefix.strip(),
            total_layers,
            options.model_name.strip(),
            useful_work_runtime=UsefulWorkRuntime(
                config=IncentivesConfig(
                    mode="off",
                    settlement_url="",
                    model_revision="tensor-payload-probe",
                ),
                max_submission_attempts=1,
                retry_base_seconds=0,
                retry_max_seconds=0,
                queue_capacity=1,
            ),
            rpc_attempt_policy=RPCAttemptPolicy(
                max_attempts=1,
                initial_backoff_seconds=0,
                max_backoff_seconds=0,
                slow_request_warning_seconds=30,
            ),
        )
        checkpoint({"stage": "validate_route"})
        node = _wait_for_controlled_route(
            sequential,
            options,
            total_layers=total_layers,
            expected_rpc_uid=expected_rpc_uid,
        )
        checkpoint({"stage": "payload_sweep", "route": _route_evidence(node)})
        cases, failure = _run_payload_sweep(
            sequence_lengths=options.sequence_lengths,
            hidden_size=hidden_size,
            node=node,
            forward_call=sequential._call_node,
            checkpoint=checkpoint,
        )
        measurement_ok = failure is None and len(cases) == len(options.sequence_lengths)
        result.update(
            {
                "stage": "measurement_complete" if measurement_ok else failure["stage"],
                "cases": cases,
                "failure": failure,
            }
        )
    except Exception as exc:
        result.update(
            {
                "failure": {"stage": result["stage"], **_failure_evidence(exc)},
                "stage": result["stage"],
            }
        )
    finally:
        result["stage"] = "cleanup"
        if dht is not None:
            result["cleanup"]["remote_expert_p2p_stopped"] = (
                shutdown_remote_expert_p2p(dht)
            )
            result["cleanup"]["client_dht_stopped"] = _run_with_timeout(
                "tensor-payload-probe-dht-shutdown",
                dht.shutdown,
                10,
            )
        cleanup_ok = all(value is True for value in result["cleanup"].values())
        result["ok"] = bool(measurement_ok and cleanup_ok)
        result["stage"] = "complete" if result["ok"] else "failed"
        if options.output is not None:
            _write_private_json(options.output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-peer", action="append", dest="initial_peers")
    parser.add_argument("--expected-peer", required=True)
    parser.add_argument(
        "--expected-transport",
        required=True,
        choices=sorted(VALID_TRANSPORTS),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dht-prefix", default=os.getenv("DISTRIBLLM_DHT_PREFIX", DHT_PREFIX))
    parser.add_argument(
        "--sequence-length",
        action="append",
        type=int,
        dest="sequence_lengths",
    )
    parser.add_argument("--route-timeout", type=float, default=60)
    parser.add_argument("--expected-rpc-uid")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_project_env()
    args = _parser().parse_args(argv)
    initial_peers = tuple(args.initial_peers or get_initial_peers())
    options = ProbeOptions(
        initial_peers=initial_peers,
        expected_peer=args.expected_peer,
        expected_transport=args.expected_transport,
        model_name=args.model,
        dht_prefix=args.dht_prefix,
        sequence_lengths=tuple(args.sequence_lengths or DEFAULT_SEQUENCE_LENGTHS),
        route_timeout=args.route_timeout,
        expected_rpc_uid=args.expected_rpc_uid,
        output=args.output,
    )
    try:
        result = run_probe(options)
    except TensorPayloadProbeError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "probe": "legacy_tensor_payload_sweep",
            "ok": False,
            "captured_at": _utc_now(),
            "stage": "configuration",
            "failure": {"stage": "configuration", **_failure_evidence(exc)},
        }
        _write_private_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
