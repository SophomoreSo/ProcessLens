"""Launcher: ``python -m miner`` starts ProcessLens Miner on ``127.0.0.1:8100``.

Miner reads the datasets of the ProcessLens installation configured by the usual
``PROCESSLENS_*`` variables and keeps its own workspace in ``<data dir>/miner.db``.
Like the ProcessLens admin site it can change data and has no login, so the
launcher refuses a non-loopback address unless
``PROCESSLENS_MINER_ALLOW_NON_LOOPBACK_BIND=true`` is set.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from app.config import LOOPBACK_HOSTS
from app.config import get_settings as processlens_settings
from miner.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m miner", description="Start ProcessLens Miner.")
    parser.add_argument("--port", type=int, help="port (default PROCESSLENS_MINER_PORT or 8100)")
    args = parser.parse_args(argv)
    settings = get_settings()
    port = args.port or settings.port
    if settings.host not in LOOPBACK_HOSTS and not settings.allow_non_loopback_bind:
        print(f"Refusing to bind ProcessLens Miner to {settings.host!r}: it can change data and has no "
              "authentication, so it is for this computer only. Set PROCESSLENS_MINER_ALLOW_NON_LOOPBACK_BIND=true "
              "only inside an isolated container whose port is published on 127.0.0.1.", file=sys.stderr)
        return 2
    if port == processlens_settings().port:
        print("PROCESSLENS_MINER_PORT must differ from the ProcessLens port.", file=sys.stderr)
        return 2
    print(f"ProcessLens Miner: http://{settings.host}:{port} (datasets from {processlens_settings().data_dir})",
          file=sys.stderr)
    uvicorn.Server(uvicorn.Config("miner.main:app", host=settings.host, port=port,
                                  log_level=processlens_settings().log_level.lower(), proxy_headers=False,
                                  server_header=False)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
