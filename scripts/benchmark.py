"""Measure end-to-end timings on a synthetic log of ~100,000 events.

    python scripts/benchmark.py [--cases 11500]

Uses a temporary data directory (your real database is untouched) and the
in-process TestClient, so timings exclude network overhead but include
JSON serialization. Prints environment details with the results.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import platform
import random
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp(prefix="processlens_bench_")
os.environ["PROCESSLENS_DATA_DIR"] = tmp
os.environ["PROCESSLENS_ALLOWED_HOSTS"] = "testserver"
os.environ["PROCESSLENS_LOG_LEVEL"] = "WARNING"


def load_generator():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "generate_demo_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_log(path: Path, n_cases: int) -> int:
    gen = load_generator()
    rng = random.Random(7)
    rows = []
    span = (gen.LAST_START_DAY - gen.FIRST_DAY).days
    for i in range(1, n_cases + 1):
        start = gen.business_time(rng, (gen.FIRST_DAY + timedelta(days=rng.randint(0, span))).replace(hour=8),
                                  rng.uniform(0, 9))
        rows.extend(gen.make_case(rng, i, start))
    for n, r in enumerate(rows, start=1):
        r["event_id"] = f"EV{n:08d}"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=gen.COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=11500)
    args = ap.parse_args()

    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import csrf_token
    from app.services import jobs
    from app.services.cache import cache
    from app.services.demo import demo_request

    path = Path(tmp) / "bench.csv"
    n_events = write_log(path, args.cases)
    results: list[tuple[str, float]] = []

    def timed(label, fn):
        t = time.perf_counter()
        out = fn()
        results.append((label, time.perf_counter() - t))
        return out

    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        up = timed("upload + preview", lambda: c.post("/api/uploads", files={"file": ("bench.csv", path.read_bytes(), "text/csv")}).json())
        req = demo_request().model_dump(mode="json")
        req["dataset_name"] = "benchmark"
        opts = {k: req[k] for k in ("mapping", "timestamps")}
        timed("validate all rows", lambda: c.post(f"/api/uploads/{up['upload_id']}/validate", json=opts).json())

        def do_import():
            job = c.post(f"/api/uploads/{up['upload_id']}/import", json=req).json()
            jobs.wait_for(job["id"], timeout=600)
            return c.get(f"/api/jobs/{job['id']}").json()
        job = timed("import job (validate + write + alerts)", do_import)
        assert job["status"] == "succeeded", job
        ds = job["dataset_id"]
        cache.clear()
        timed("overview (cold: load events from DB)", lambda: c.get(f"/api/datasets/{ds}/overview").json())
        timed("overview (cached)", lambda: c.get(f"/api/datasets/{ds}/overview").json())
        timed("process graph", lambda: c.get(f"/api/datasets/{ds}/graph").json())
        timed("variants", lambda: c.get(f"/api/datasets/{ds}/variants").json())
        timed("cases page 1", lambda: c.get(f"/api/datasets/{ds}/cases").json())
        timed("case detail", lambda: c.get(f"/api/datasets/{ds}/cases/0004500001").json())
        timed("performance", lambda: c.get(f"/api/datasets/{ds}/performance").json())
        timed("filtered overview (activity + department)",
              lambda: c.get(f"/api/datasets/{ds}/overview", params={"activity": "Change Price", "department": "IT"}).json())
        rule = c.post(f"/api/datasets/{ds}/conformance-rules", json={
            "name": "Approve before sending", "rule_type": "precedence",
            "params": {"activity_a": "Approve Purchase Order", "activity_b": "Send Purchase Order"}}).json()
        for body in ({"name": "SLA", "alert_type": "duration_threshold", "params": {"threshold_hours": 720, "metric": "completed_throughput"}},
                     {"name": "Rework", "alert_type": "rework", "params": {}},
                     {"name": "Unapproved", "alert_type": "conformance_violation", "conformance_rule_id": rule["id"]}):
            c.post(f"/api/datasets/{ds}/alert-rules", json=body)
        timed("conformance evaluation (precedence)", lambda: c.get(f"/api/datasets/{ds}/conformance-rules/{rule['id']}/evaluation").json())
        timed("alert evaluation (3 rules, first run)", lambda: c.post(f"/api/datasets/{ds}/alert-rules/evaluate").json())
        timed("alert evaluation (re-run, idempotent)", lambda: c.post(f"/api/datasets/{ds}/alert-rules/evaluate").json())
        timed("events CSV export", lambda: c.get(f"/api/datasets/{ds}/exports/events.csv").content)

    print(f"\nProcessLens benchmark — {n_events:,} events, {args.cases:,} cases")
    print(f"Python {platform.python_version()} · {platform.platform()} · {platform.machine()} · "
          f"{os.cpu_count()} logical CPUs · SQLite (file in temp dir)")
    for label, secs in results:
        print(f"  {label:<45} {secs:7.2f} s")


if __name__ == "__main__":
    main()
