"""
Minimal Hivemind relay reservation probe.

This intentionally excludes FastAPI, model loading, and expert routing. It only
starts a DHT peer with the same relay-facing arguments as a serving worker and
waits for a visible p2p-circuit multiaddress.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import hivemind
from hivemind.utils.logging import get_logger

from api.env_loader import load_project_env
from constants import P2PNetworkConfig, get_initial_peers, get_p2p_network_config
from node.relay_compat import install_static_relay_compat

logger = get_logger(__name__)


@dataclass(frozen=True)
class RelayProbeResult:
    ok: bool
    peer_id: str | None
    circuit_maddrs: list[str]
    visible_maddrs: list[str]
    elapsed_seconds: float
    initial_peers: list[str]
    trusted_relays: list[str]
    python_version: str = platform.python_version()
    hivemind_version: str = hivemind.__version__
    force_reachability: str = "private"
    relay_discovery: bool = False
    error: str | None = None


def _relay_dht_kwargs(
    *,
    initial_peers: list[str],
    p2p_config: P2PNetworkConfig,
) -> dict[str, Any]:
    return {
        "host_maddrs": p2p_config.host_maddrs,
        "announce_maddrs": list(p2p_config.announce_maddrs) or None,
        "initial_peers": initial_peers,
        "start": True,
        "use_ipfs": False,
        "auto_nat": p2p_config.auto_nat,
        "nat_port_map": p2p_config.nat_port_map,
        "use_relay": True,
        "use_auto_relay": p2p_config.use_auto_relay,
        "trusted_relays": list(p2p_config.trusted_relays) or None,
        "client_mode": True,
        "force_reachability": "private",
    }


def run_relay_probe(
    *,
    initial_peers: list[str] | None = None,
    p2p_config: P2PNetworkConfig | None = None,
    timeout: float | None = None,
) -> RelayProbeResult:
    config = p2p_config or get_p2p_network_config()
    peers = initial_peers if initial_peers is not None else get_initial_peers()
    wait_timeout = config.relay_wait_timeout if timeout is None else timeout

    if not peers:
        return RelayProbeResult(
            ok=False,
            peer_id=None,
            circuit_maddrs=[],
            visible_maddrs=[],
            elapsed_seconds=0.0,
            initial_peers=[],
            trusted_relays=list(config.trusted_relays),
            error="Relay probe requires at least one bootstrap or relay peer.",
        )
    if not config.use_auto_relay:
        return RelayProbeResult(
            ok=False,
            peer_id=None,
            circuit_maddrs=[],
            visible_maddrs=[],
            elapsed_seconds=0.0,
            initial_peers=peers,
            trusted_relays=list(config.trusted_relays),
            error="Relay probe requires DISTRIBLLM_AUTO_RELAY=true.",
        )

    dht = None
    started_at = time.monotonic()
    visible_maddrs: list[str] = []
    try:
        install_static_relay_compat()
        kwargs = _relay_dht_kwargs(initial_peers=peers, p2p_config=config)
        logger.info("Starting relay probe DHT with args: %s", kwargs)
        dht = hivemind.DHT(**kwargs)
        peer_id = str(dht.peer_id) if dht.peer_id is not None else None
        deadline = started_at + max(0.0, wait_timeout)

        while True:
            visible_maddrs = [
                str(addr) for addr in dht.get_visible_maddrs(latest=True)
            ]
            circuit_maddrs = [
                address
                for address in visible_maddrs
                if "/p2p-circuit" in address
            ]
            if circuit_maddrs:
                return RelayProbeResult(
                    ok=True,
                    peer_id=peer_id,
                    circuit_maddrs=circuit_maddrs,
                    visible_maddrs=visible_maddrs,
                    elapsed_seconds=time.monotonic() - started_at,
                    initial_peers=peers,
                    trusted_relays=list(config.trusted_relays),
                )
            if time.monotonic() >= deadline:
                return RelayProbeResult(
                    ok=False,
                    peer_id=peer_id,
                    circuit_maddrs=[],
                    visible_maddrs=visible_maddrs,
                    elapsed_seconds=time.monotonic() - started_at,
                    initial_peers=peers,
                    trusted_relays=list(config.trusted_relays),
                    error=(
                        "No /p2p-circuit/ address became visible before "
                        f"{wait_timeout:g} seconds elapsed. The participant "
                        "was forced to private reachability; inspect the VPS "
                        "relay-service logs and runtime version."
                    ),
                )
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    except Exception as e:
        return RelayProbeResult(
            ok=False,
            peer_id=None,
            circuit_maddrs=[],
            visible_maddrs=visible_maddrs,
            elapsed_seconds=time.monotonic() - started_at,
            initial_peers=peers,
            trusted_relays=list(config.trusted_relays),
            error=str(e),
        )
    finally:
        if dht is not None:
            try:
                dht.shutdown()
            except Exception as e:
                logger.warning("Relay probe DHT shutdown failed: %s", e)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe whether the configured VPS relay provides a circuit address."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to wait; defaults to DISTRIBLLM_RELAY_WAIT_TIMEOUT.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON for copying into diagnostics.",
    )
    return parser.parse_args()


def main() -> int:
    load_project_env()
    args = parse_args()
    result = run_relay_probe(timeout=args.timeout)

    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"Relay probe: {status}")
        print(f"Peer ID: {result.peer_id or '<unavailable>'}")
        print(
            f"Runtime: Python {result.python_version} | "
            f"Hivemind {result.hivemind_version}"
        )
        print(f"Forced reachability: {result.force_reachability}")
        print("Relay selection: static trusted relays")
        print(f"Elapsed: {result.elapsed_seconds:.1f}s")
        print("Initial peers:")
        for peer in result.initial_peers:
            print(f"  {peer}")
        print("Trusted relays:")
        for relay in result.trusted_relays:
            print(f"  {relay}")
        print("Visible addresses:")
        for address in result.visible_maddrs:
            print(f"  {address}")
        if result.error:
            print(f"Error: {result.error}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
