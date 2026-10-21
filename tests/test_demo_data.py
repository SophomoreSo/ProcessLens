"""The committed demo files must be exactly what the deterministic generator produces."""

from __future__ import annotations

import csv
import json
import importlib.util
import io

from app.config import PROJECT_ROOT


def load_generator(name: str = "generate_demo_data.py"):
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), PROJECT_ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo_file_matches_generator():
    gen = load_generator()
    rows = gen.generate_events()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=gen.COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    committed = (PROJECT_ROOT / "sample_data" / "p2p_demo_events.csv").read_text(encoding="utf-8")
    assert buf.getvalue() == committed
    assert gen.generate_events() == rows  # deterministic


def test_demo_content_covers_required_scenarios():
    rows = load_generator().generate_events()
    cases: dict[str, list[dict]] = {}
    for r in rows:
        cases.setdefault(r["case_id"], []).append(r)
    assert len(cases) >= 500
    acts = [[e["activity"] for e in sorted(evs, key=lambda e: e["timestamp"])] for evs in cases.values()]
    assert any("Change Price" in a for a in acts)  # rework loop
    assert any(a.count("Receive Invoice") > 1 for a in acts)  # invoice rework
    assert any("Approve Purchase Order" not in a and "Send Purchase Order" in a for a in acts)  # missing approval
    statuses = {evs[0]["case_status"] for evs in cases.values()}
    assert {"Completed", "Open", "Cancelled"} <= statuses
    assert len({e["department"] for e in rows}) >= 6 and len({e["resource"] for e in rows}) >= 15
    assert len({tuple(a) for a in acts}) >= 10
    assert all(cid.startswith("00") for cid in cases)  # leading zeros


def test_ocel_and_task_demo_files_match_generators(tmp_path):
    ocel_gen = load_generator("generate_ocel_demo.py")
    out = tmp_path / "ocel.json"
    data = ocel_gen.generate()
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert out.read_text(encoding="utf-8") == (PROJECT_ROOT / "sample_data" / "ocel_demo.json").read_text(encoding="utf-8")
    assert ocel_gen.generate() == data

    task_gen = load_generator("generate_task_demo.py")
    rows = task_gen.generate()
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    committed = (PROJECT_ROOT / "sample_data" / "task_events_demo.csv").read_bytes().decode("utf-8")
    assert buf.getvalue() == committed
    assert all("@" not in r["pseudonymous_user_id"] for r in rows)
