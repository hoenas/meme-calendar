"""Entrypoint: `memecal` bzw. `python -m memecal`."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Meme-Kalender starten")
    parser.add_argument("--host", default=os.environ.get("MEMECAL_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MEMECAL_PORT", "8000"))
    )
    parser.add_argument("--reload", action="store_true", help="Autoreload (nur dev)")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("MEMECAL_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    uvicorn.run(
        "memecal.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # Der Reverse-Proxy setzt X-Forwarded-*.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
