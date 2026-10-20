"""Launcher: ``python -m app``.

Refuses to bind to a non-loopback interface unless
``PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true`` is set explicitly (the app has no
authentication).
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import LOOPBACK_HOSTS, get_settings


def main() -> int:
    settings = get_settings()
    if settings.host not in LOOPBACK_HOSTS and not settings.allow_non_loopback_bind:
        print(
            f"Refusing to bind to {settings.host!r}: ProcessLens has no authentication and is meant for "
            "localhost only. Set PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true only inside an isolated container "
            "whose port is published on 127.0.0.1.",
            file=sys.stderr,
        )
        return 2
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower(),
                proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
