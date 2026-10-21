"""Launcher: ``python -m app`` (admin site) and ``python -m app --guest`` (admin + guest site).

* The **admin** site (full read/write access) listens on ``PROCESSLENS_HOST:PROCESSLENS_PORT``
  (default ``127.0.0.1:8000``). The launcher refuses a non-loopback admin address unless
  ``PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true`` is set explicitly (for an isolated container
  whose port is published on 127.0.0.1 only).
* The **guest** site (read-only layout for other people) is started with ``--guest`` or
  ``PROCESSLENS_GUEST_ENABLED=true`` and listens on ``PROCESSLENS_GUEST_HOST:PROCESSLENS_GUEST_PORT``
  (default ``0.0.0.0:8080``). It has no login: everyone who can reach that address can read
  all data, but nothing can be changed through it.

Both sites run in one process and share the database, caches and the mining worker.
"""

from __future__ import annotations

import argparse
import sys
import threading

import uvicorn

from app.config import LOOPBACK_HOSTS, get_settings


def _server(app_path: str, host: str, port: int, log_level: str) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(app_path, host=host, port=port, log_level=log_level,
                                         proxy_headers=False, server_header=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Start ProcessLens.")
    parser.add_argument("--guest", action="store_true", help="also start the read-only guest site")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.host not in LOOPBACK_HOSTS and not settings.allow_non_loopback_bind:
        print(
            f"Refusing to bind the admin site to {settings.host!r}: it has full write access and no "
            "authentication, so it is for this computer only. Use the read-only guest site (--guest) for other "
            "people. Set PROCESSLENS_ALLOW_NON_LOOPBACK_BIND=true only inside an isolated container whose port "
            "is published on 127.0.0.1.",
            file=sys.stderr,
        )
        return 2
    level = settings.log_level.lower()
    admin = _server("app.main:app", settings.host, settings.port, level)
    if not (args.guest or settings.guest_enabled):
        admin.run()
        return 0
    if settings.guest_port == settings.port:
        print("PROCESSLENS_GUEST_PORT must differ from PROCESSLENS_PORT.", file=sys.stderr)
        return 2
    from app.migrate import upgrade_to_head

    upgrade_to_head()  # once, before the two sites start (their start-up checks are then no-ops)
    guest = _server("app.main:guest_app", settings.guest_host, settings.guest_port, level)
    print(f"Admin site (read/write, this computer only): http://{settings.host}:{settings.port}\n"
          f"Guest site (read-only, no login, anyone who can reach it): "
          f"http://{settings.guest_host}:{settings.guest_port}", file=sys.stderr)
    # The guest server runs in a thread (uvicorn installs signal handlers only in the main
    # thread); stopping the admin server with Ctrl+C stops the guest server too.
    thread = threading.Thread(target=guest.run, name="guest-site", daemon=True)
    thread.start()
    try:
        admin.run()
    finally:
        guest.should_exit = True
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
