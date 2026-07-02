"""
main.py
Entry point for the DistribLLM backend.

Usage:
    python main.py
    python main.py --host 0.0.0.0 --port 8000
"""

import argparse
import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DistribLLM Backend")
    parser.add_argument("--host",   type=str,  default="0.0.0.0")
    parser.add_argument("--port",   type=int,  default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(f"Starting DistribLLM backend on {args.host}:{args.port}")

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        timeout_graceful_shutdown=5,
    )


