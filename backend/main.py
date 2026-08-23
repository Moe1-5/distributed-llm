"""
main.py
Entry point for the DistribLLM backend.

Usage:
    python main.py
    python main.py --host 127.0.0.1 --port 8000
"""

import argparse
import ipaddress
import uvicorn


def validate_backend_host(host: str) -> str:
    """Reject network-exposed management binds until authenticated TLS exists."""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return normalized
    try:
        if ipaddress.ip_address(normalized).is_loopback:
            return normalized
    except ValueError:
        pass
    raise ValueError(
        "DistribLLM management API must bind to localhost or a loopback IP; "
        "non-loopback operation requires a separately authenticated TLS gateway"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DistribLLM Backend")
    parser.add_argument("--host",   type=str,  default="127.0.0.1")
    parser.add_argument("--port",   type=int,  default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    args.host = validate_backend_host(args.host)
    print(f"Starting DistribLLM backend on {args.host}:{args.port}")

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        timeout_graceful_shutdown=5,
    )
