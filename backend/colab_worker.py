"""Run a headless DistribLLM serving node from Colab or another GPU host."""

from __future__ import annotations

import argparse
import os
import signal
import time

import torch

from api.hf_oauth import poll_huggingface_device_flow, start_huggingface_device_flow
from api.settings import get_hf_token
from constants import DHT_PREFIX, get_initial_peers
from node.node import Node


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start a headless DistribLLM worker")
    parser.add_argument("--model", required=True, help="Supported Hugging Face model ID")
    parser.add_argument("--layer-start", required=True, type=int)
    parser.add_argument("--layer-end", required=True, type=int)
    parser.add_argument("--peer", action="append", default=[], help="Bootstrap multiaddr; repeat for multiple peers")
    parser.add_argument("--dht-prefix", default=os.environ.get("DISTRIBLLM_DHT_PREFIX", DHT_PREFIX))
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument(
        "--oauth-client-id",
        default=os.environ.get("DISTRIBLLM_HF_OAUTH_CLIENT_ID"),
        help="Hugging Face OAuth client ID used for browser device authorization",
    )
    return parser.parse_args()


def authenticate_huggingface(client_id: str | None) -> str | None:
    token = get_hf_token() or os.environ.get("HF_TOKEN")
    if token or not client_id:
        return token

    os.environ["DISTRIBLLM_HF_OAUTH_CLIENT_ID"] = client_id
    flow = start_huggingface_device_flow()
    authorization_url = flow.get("verification_uri_complete") or flow["verification_uri"]
    print(f"Open this URL in your browser: {authorization_url}", flush=True)
    print(f"Enter authorization code: {flow['user_code']}", flush=True)

    interval = int(flow["interval"])
    while True:
        time.sleep(interval)
        result = poll_huggingface_device_flow(flow["flow_id"])
        if result["status"] == "connected":
            print("Hugging Face OAuth connected.", flush=True)
            return get_hf_token()
        interval = int(result.get("interval", interval))


def main() -> None:
    args = parse_args()
    peers = args.peer or get_initial_peers()
    token = authenticate_huggingface(args.oauth_client_id)
    dtype = torch.float16 if args.device == "cuda" else torch.float32

    node = Node(
        model_name=args.model,
        layer_start=args.layer_start,
        layer_end=args.layer_end,
        dht_prefix=args.dht_prefix,
        initial_peers=peers,
        device=args.device,
        dtype=dtype,
        hf_token=token,
    )

    stopping = False

    def stop_node(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop_node)
    signal.signal(signal.SIGTERM, stop_node)

    node.start()
    print("DistribLLM worker started:", node.get_info(), flush=True)
    try:
        while not stopping:
            time.sleep(5)
    finally:
        node.stop()


if __name__ == "__main__":
    main()
