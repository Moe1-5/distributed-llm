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

Usage:
    First run (generates identity):
        python3 bootstrap.py --port 7001 --identity_path bootstrap.id

    Subsequent runs (stable address):
        python3 bootstrap.py --port 7001 --identity_path bootstrap.id

    After first run, copy the printed /ip4/<IP>/tcp/<PORT>/p2p/<PEER_ID>
    into constants.py as DISTRIBLLM_INITIAL_PEERS.
"""

import argparse
import time
import signal
import sys

import hivemind
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DistribLLM Bootstrap Node")
    parser.add_argument(
        "--port", type=int, default=7001,
        help="TCP port to listen on (default: 7001)"
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  DistribLLM Bootstrap Node")
    print("=" * 60)

    first_run = not __import__("os").path.exists(args.identity_path)
    if first_run:
        print(f"  First run — generating new identity at: {args.identity_path}")
        print(f"  IMPORTANT: Keep {args.identity_path} safe.")
        print(f"  Deleting it will change your peer ID and break existing nodes.")
    else:
        print(f"  Loading existing identity from: {args.identity_path}")
    print()

    dht = hivemind.DHT(
        host_maddrs=[f"/ip4/{args.host}/tcp/{args.port}"],
        start=True,
        # identity_path makes peer ID deterministic across restarts
        identity_path=args.identity_path,
        # use_ipfs=False keeps us off the Petals/IPFS public network
        use_ipfs=False,
        # No initial peers — this IS the bootstrap node
        initial_peers=[],
    )

    assert dht.peer_id is not None, "DHT started but peer_id is None"

    visible = [str(addr) for addr in dht.get_visible_maddrs()]

    print("  Bootstrap addresses (share these with your nodes):")
    for addr in visible:
        print(f"    {addr}")
    print()
    print("  Copy ONE of these into backend/constants.py")
    print("  as DISTRIBLLM_INITIAL_PEERS (use the public IP one).")
    print()
    print("  Keep this process running — nodes need it to join the swarm.")
    print("=" * 60)

    # Graceful shutdown on Ctrl+C
    def _shutdown(sig, frame):
        print("\nShutting down bootstrap node...")
        dht.shutdown()
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
