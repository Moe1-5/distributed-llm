"""
bootstrap.py
Runs a stable DHT entry point for the DistribLLM swarm.

Key behaviour:
    --identity_path  saves the P2P private key to a file on first run.
                     Every subsequent restart loads the same key,
                     producing the same peer ID and therefore the
                     same multiaddress. This is what makes the
                     bootstrap address stable.

    use_ipfs=False   keeps us off the Petals/IPFS public network.

    --role dht       runs a full DHT storage/bootstrap peer without relay.

    --role relay     runs a circuit relay as a DHT client, so it does not
                     store provider records. It must join through one or more
                     full DHT peers supplied with --initial-peer.

    --role combined  preserves the legacy single-process deployment for
                     rollback only.

Usage:
    First run (generates identity):
        python3 bootstrap.py --port 7001 --identity_path bootstrap.id

    Subsequent runs (stable address):
        python3 bootstrap.py --port 7001 --identity_path bootstrap.id

    After first run, configure the printed public multiaddress as
    DISTRIBLLM_INITIAL_PEERS and DISTRIBLLM_TRUSTED_RELAYS on participants.
"""

import argparse
import json
import os
import platform
import time
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hivemind
from hivemind.utils.logging import get_logger
from node.reachability import ReachabilityProtocol

logger = get_logger(__name__)

INFRASTRUCTURE_PROTOCOL_VERSION = 1
VALID_ROLES = {"dht", "relay", "combined"}


def _storage_enabled(args: argparse.Namespace) -> bool:
    return args.role in {"dht", "combined"}


def _relay_enabled(args: argparse.Namespace) -> bool:
    return args.role in {"relay", "combined"} and bool(args.use_relay)


def _validate_role_args(args: argparse.Namespace) -> None:
    if args.role not in VALID_ROLES:
        raise ValueError(f"Unsupported infrastructure role: {args.role}")
    if args.role == "relay" and not args.initial_peer:
        raise ValueError("Relay role requires at least one --initial-peer DHT address")
    if args.role == "relay" and not args.use_relay:
        raise ValueError("Relay role cannot be combined with --no-relay")


def _bootstrap_dht_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Build the public infrastructure peer's Hivemind transport options."""
    _validate_role_args(args)
    storage_enabled = _storage_enabled(args)
    return {
        "host_maddrs": [f"/ip4/{args.host}/tcp/{args.port}"],
        "announce_maddrs": args.announce_maddr or None,
        "start": True,
        "identity_path": args.identity_path,
        "use_ipfs": False,
        "initial_peers": list(args.initial_peer),
        "use_relay": _relay_enabled(args),
        "force_reachability": "public" if args.announce_maddr else None,
        "client_mode": not storage_enabled,
        "cache_locally": storage_enabled,
    }


def _bootstrap_status(
    args: argparse.Namespace,
    peer_id: str,
    visible_maddrs: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "infrastructure_protocol_version": INFRASTRUCTURE_PROTOCOL_VERSION,
        "role": args.role,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "peer_id": peer_id,
        "visible_maddrs": visible_maddrs,
        "python_version": platform.python_version(),
        "hivemind_version": hivemind.__version__,
        "deployment_commit": args.deployment_commit or None,
        "failure_domain": args.failure_domain or None,
        "identity_path": str(Path(args.identity_path).resolve()),
        "relay_enabled": _relay_enabled(args),
        "dht_storage_enabled": _storage_enabled(args),
        "force_reachability": "public" if args.announce_maddr else "automatic",
        "host": args.host,
        "port": args.port,
        "announce_maddrs": list(args.announce_maddr),
        "initial_peers": list(args.initial_peer),
    }


def _write_status(path: str, status: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{json.dumps(status, indent=2, sort_keys=True)}\n", encoding="utf-8")
    os.chmod(temporary, 0o640)
    temporary.replace(target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DistribLLM Bootstrap Node")
    parser.add_argument(
        "--role",
        choices=sorted(VALID_ROLES),
        default="combined",
        help="Infrastructure responsibility (default: combined rollback mode).",
    )
    parser.add_argument(
        "--port", type=int, default=7001,
        help="TCP port to listen on (default: 7001)"
    )
    parser.add_argument(
        "--initial-peer",
        action="append",
        default=[],
        help="Full DHT peer multiaddress; required by relay role and repeatable.",
    )
    parser.add_argument(
        "--status-path",
        type=str,
        default="",
        help="Optional path for atomic non-secret runtime status JSON.",
    )
    parser.add_argument(
        "--deployment-commit",
        type=str,
        default="",
        help="Commit identifier recorded in runtime status for operator validation.",
    )
    parser.add_argument(
        "--failure-domain",
        type=str,
        default="",
        help="Stable operator label for the host/provider failure domain.",
    )
    parser.add_argument(
        "--identity_path", type=str, default="bootstrap.id",
        help="Path to save/load P2P identity key (default: bootstrap.id). "
             "IMPORTANT: keep this file — deleting it changes your peer ID."
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="Host IP to bind to (default: 0.0.0.0 = all interfaces)"
    )
    parser.add_argument(
        "--announce-maddr",
        action="append",
        default=[],
        help=(
            "Public multiaddr to announce; repeat for multiple addresses. "
            "Example: /ip4/203.0.113.10/tcp/7001"
        ),
    )
    parser.add_argument(
        "--no-relay",
        action="store_false",
        dest="use_relay",
        help="Disable libp2p circuit-relay forwarding.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        _validate_role_args(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("=" * 60)
    print(f"  DistribLLM Infrastructure Peer ({args.role})")
    print("=" * 60)

    first_run = not os.path.exists(args.identity_path)
    if first_run:
        print(f"  First run — generating new identity at: {args.identity_path}")
        print(f"  IMPORTANT: Keep {args.identity_path} safe.")
        print(f"  Deleting it will change your peer ID and break existing nodes.")
    else:
        print(f"  Loading existing identity from: {args.identity_path}")
    print(
        f"  Runtime: Python {platform.python_version()} | "
        f"Hivemind {hivemind.__version__}"
    )
    print(
        "  DHT storage: "
        f"{'enabled' if _storage_enabled(args) else 'disabled (client mode)'}"
    )
    print(
        "  Relay transport/service: "
        f"{'enabled' if _relay_enabled(args) else 'disabled'}"
    )
    print(
        "  Forced reachability: "
        f"{'public' if args.announce_maddr else 'automatic'}"
    )
    print()

    dht = hivemind.DHT(**_bootstrap_dht_kwargs(args))

    assert dht.peer_id is not None, "DHT started but peer_id is None"

    visible = [str(addr) for addr in dht.get_visible_maddrs()]
    reachability_protocol = ReachabilityProtocol.attach_to_dht(
        dht,
        await_ready=True,
    )
    if args.status_path:
        _write_status(
            args.status_path,
            _bootstrap_status(args, str(dht.peer_id), visible),
        )

    print("  Bootstrap addresses (share these with your nodes):")
    for addr in visible:
        print(f"    {addr}")
    print()
    if args.role == "dht":
        print("  Add this address to participant DISTRIBLLM_INITIAL_PEERS.")
    elif args.role == "relay":
        print("  Add this address to participant DISTRIBLLM_TRUSTED_RELAYS.")
    else:
        print("  Legacy combined mode: use this address in both peer lists.")
    print()
    print("  Keep this process running — nodes need it to join the swarm.")
    print("=" * 60)

    # Graceful shutdown on Ctrl+C
    def _shutdown(sig, frame):
        print("\nShutting down bootstrap node...")
        reachability_protocol.shutdown()
        dht.shutdown()
        if args.status_path:
            Path(args.status_path).unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Keep alive
    while True:
        time.sleep(30)
        peers = dht.get_visible_maddrs()
        logger.debug(f"Bootstrap alive | visible addrs: {len(peers)}")


if __name__ == "__main__":
    main()
