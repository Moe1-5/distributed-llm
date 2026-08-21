"""Independent DHT lease observer for physical relay acceptance.

Run this as a separate process from both the worker and generator. It creates
its own Hivemind client identity and requires records to remain visible beyond
the next observation interval. JSON Lines output is suitable for preserving as
acceptance evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import hivemind
from hivemind.moe.server.dht_handler import get_experts
from hivemind.utils import get_dht_time

from constants import DHT_OPERATION_TIMEOUT, get_initial_peers


def _member_leases(dht, dht_prefix: str) -> dict[str, dict]:
    members: dict[str, dict] = {}
    result = dht.get(f"{dht_prefix}.members.v2", latest=True)
    if result is not None and isinstance(result.value, dict):
        for _subkey, wrapped in result.value.items():
            lease = getattr(wrapped, "value", wrapped)
            expiration = getattr(wrapped, "expiration_time", None)
            if not isinstance(lease, dict):
                continue
            peer_id = lease.get("peer_id")
            if isinstance(peer_id, str) and peer_id:
                members[peer_id] = {
                    "source": "members.v2",
                    "expiration_time": expiration,
                }

    legacy = dht.get(f"{dht_prefix}.members", latest=True)
    if legacy is not None and isinstance(legacy.value, list):
        for peer_id in legacy.value:
            if isinstance(peer_id, str) and peer_id:
                members.setdefault(
                    peer_id,
                    {
                        "source": "members",
                        "expiration_time": legacy.expiration_time,
                    },
                )
    return members


def observe_once(
    dht,
    *,
    dht_prefix: str,
    model_name: str,
    layer_start: int,
    layer_end: int,
    expected_peer: Optional[str],
    required_horizon_seconds: float,
    require_receipt: bool,
) -> dict:
    observed_dht_time = get_dht_time()
    required_expiration = observed_dht_time + required_horizon_seconds
    members = _member_leases(dht, dht_prefix)
    candidates: list[dict] = []

    for peer_id, member in members.items():
        if expected_peer and peer_id != expected_peer:
            continue
        metadata = dht.get(
            f"{dht_prefix}.node_info.{peer_id}",
            latest=True,
        )
        if metadata is None or not isinstance(metadata.value, dict):
            continue
        value = metadata.value
        if (
            value.get("model_name") != model_name
            or value.get("layer_start") != layer_start
            or value.get("layer_end") != layer_end
        ):
            continue
        candidates.append(
            {
                "peer_id": peer_id,
                "member": member,
                "metadata": value,
                "metadata_expiration_time": metadata.expiration_time,
            }
        )

    reasons: list[str] = []
    provider_results: list[dict] = []
    if not candidates:
        identity = expected_peer or (
            f"{model_name} layers {layer_start}-{layer_end}"
        )
        reasons.append(f"Provider {identity} was not visible")

    for candidate in candidates:
        metadata = candidate["metadata"]
        peer_id = candidate["peer_id"]
        uids = [metadata.get("rpc_uid")]
        receipt_uid = metadata.get("receipt_rpc_uid")
        if require_receipt:
            uids.append(receipt_uid)
        valid_uids = [uid for uid in uids if isinstance(uid, str) and uid]
        if len(valid_uids) != len(uids):
            reasons.append(f"Provider {peer_id} is missing a required expert UID")

        experts = (
            get_experts(
                dht,
                valid_uids,
                expiration_time=required_expiration,
            )
            if valid_uids
            else []
        )
        expert_results = []
        for uid, expert in zip(valid_uids, experts):
            resolved_peer = str(expert.peer_id) if expert is not None else None
            matches = resolved_peer == peer_id
            expert_results.append(
                {
                    "uid": uid,
                    "resolved_peer_id": resolved_peer,
                    "matches_provider": matches,
                }
            )
            if not matches:
                reasons.append(
                    f"Expert {uid} did not resolve to provider {peer_id}"
                )

        metadata_expiration = candidate["metadata_expiration_time"]
        metadata_horizon_ok = bool(
            isinstance(metadata_expiration, (int, float))
            and metadata_expiration >= required_expiration
        )
        if not metadata_horizon_ok:
            reasons.append(
                f"Provider metadata for {peer_id} does not cover the next observation interval"
            )
        member_expiration = candidate["member"].get("expiration_time")
        member_horizon_ok = bool(
            isinstance(member_expiration, (int, float))
            and member_expiration >= required_expiration
        )
        if candidate["member"]["source"] == "members.v2" and not member_horizon_ok:
            reasons.append(
                f"Member lease for {peer_id} does not cover the next observation interval"
            )

        provider_results.append(
            {
                "peer_id": peer_id,
                "node_id": metadata.get("node_id"),
                "rpc_uid": metadata.get("rpc_uid"),
                "receipt_rpc_uid": receipt_uid,
                "member_source": candidate["member"]["source"],
                "member_expiration_time": member_expiration,
                "member_horizon_ok": member_horizon_ok,
                "metadata_expiration_time": metadata_expiration,
                "metadata_horizon_ok": metadata_horizon_ok,
                "running": metadata.get("running"),
                "rpc_running": metadata.get("rpc_running"),
                "rpc_publication": metadata.get("rpc_publication"),
                "experts": expert_results,
            }
        )

    return {
        "ok": not reasons,
        "observed_at": time.time(),
        "observed_dht_time": observed_dht_time,
        "observer_peer_id": str(dht.peer_id),
        "required_expiration": required_expiration,
        "providers": provider_results,
        "reasons": reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe DistribLLM provider and expert leases independently"
    )
    parser.add_argument("--initial-peer", action="append", default=[])
    parser.add_argument("--dht-prefix", default="distribllm")
    parser.add_argument("--model", default="facebook/opt-125m")
    parser.add_argument("--layer-start", type=int, default=0)
    parser.add_argument("--layer-end", type=int, default=12)
    parser.add_argument("--expected-peer")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--require-receipt", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.interval <= 0 or args.duration < 0:
        raise SystemExit("--interval must be positive and --duration non-negative")
    peers = args.initial_peer or get_initial_peers()
    if not peers:
        raise SystemExit(
            "No bootstrap peers configured; pass --initial-peer or set "
            "DISTRIBLLM_INITIAL_PEERS"
        )

    dht = hivemind.DHT(
        initial_peers=peers,
        start=True,
        use_ipfs=False,
        use_relay=True,
        client_mode=True,
    )
    failed = False
    started_at = time.monotonic()
    try:
        while True:
            try:
                snapshot = observe_once(
                    dht,
                    dht_prefix=args.dht_prefix,
                    model_name=args.model,
                    layer_start=args.layer_start,
                    layer_end=args.layer_end,
                    expected_peer=args.expected_peer,
                    required_horizon_seconds=(
                        args.interval + DHT_OPERATION_TIMEOUT
                    ),
                    require_receipt=args.require_receipt,
                )
            except Exception as exc:
                snapshot = {
                    "ok": False,
                    "observed_at": time.time(),
                    "observer_peer_id": str(dht.peer_id),
                    "providers": [],
                    "reasons": [f"{type(exc).__name__}: {exc}"],
                }
            failed = failed or not snapshot["ok"]
            print(json.dumps(snapshot, sort_keys=True), flush=True)

            elapsed = time.monotonic() - started_at
            if args.duration <= 0 or elapsed >= args.duration:
                break
            time.sleep(min(args.interval, max(0.0, args.duration - elapsed)))
    finally:
        dht.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
