"""Upgrade of a pre-0007 (case-centric) database to object-centric datasets."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, get_settings
from app.security import csrf_token
from tests.conftest import HAND_CHECK

OCEL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "ocel_small.json"


def _alembic(url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _legacy_database(path) -> None:
    """A 0006 database holding the hand-check log as a case-centric dataset, plus a standalone OCEL log."""
    conn = sqlite3.connect(path)
    now = datetime(2024, 1, 7).isoformat(sep=" ")
    conn.execute(
        "INSERT INTO datasets (id, name, is_demo, status, version, settings_revision, source_filename, source_sha256,"
        " source_size_bytes, import_options, imported_at, created_at, source_row_count, event_count, case_count,"
        " quarantined_count, error_count, warning_count, display_timezone, completion_mode, completion_activities,"
        " completed_status_values, open_status_values, reference_time, attribute_columns)"
        " VALUES (1, 'legacy', 0, 'ready', 3, 2, 'hand_check.csv', 'x', 1, '{}', ?, ?, 16, 16, 5, 0, 0, 0, 'UTC',"
        " 'case_status', '[]', '[\"Completed\"]', '[\"Open\"]', '2024-01-06 00:00:00', '[]')", (now, now))
    df = pd.read_csv(HAND_CHECK, dtype=str, keep_default_na=False)
    df["row"] = range(1, len(df) + 1)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["case_id", "ts", "row"], kind="mergesort")
    df["seq"] = df.groupby("case_id").cumcount()
    for r in df.itertuples():
        conn.execute(
            "INSERT INTO events (dataset_id, source_row, seq, case_id, activity, ts, start_ts, event_id, resource,"
            " department, cost, case_status, attributes) VALUES (1, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)",
            (r.row, r.seq, r.case_id, r.activity, r.ts.tz_localize(None).isoformat(sep=" "), r.resource or None,
             r.department or None, float(r.cost) if r.cost else None, r.status or None,
             json.dumps({"note": f"row {r.row}"}) if r.row == 2 else None))
    conn.execute("INSERT INTO conformance_rules (dataset_id, name, rule_type, params, enabled, revision, created_at,"
                 " updated_at) VALUES (1, 'D at end', 'end_activity', ?, 1, 1, ?, ?)",
                 (json.dumps({"activities": ["D"], "assume_complete": True}), now, now))
    conn.execute(
        "INSERT INTO ocel_logs (id, name, is_demo, version, source_filename, source_sha256, source_size_bytes,"
        " event_types, object_types, counts, findings, display_timezone, imported_at)"
        " VALUES (7, 'standalone', 0, 1, 's.json', 'y', 1, '[]', ?, ?, '[]', 'UTC', ?)",
        (json.dumps([{"name": "order", "attributes": []}, {"name": "item", "attributes": []}]),
         json.dumps({"events": 2}), now))
    conn.execute("INSERT INTO ocel_events (log_id, event_id, event_type, ts, position) VALUES"
                 " (7, 'e1', 'place', '2024-01-01 08:00:00', 0), (7, 'e2', 'pick', '2024-01-01 09:00:00', 1)")
    conn.execute("INSERT INTO ocel_objects (log_id, object_id, object_type) VALUES"
                 " (7, 'o1', 'order'), (7, 'i1', 'item'), (7, 'i2', 'item')")
    conn.execute("INSERT INTO ocel_e2o (log_id, event_id, object_id, qualifier) VALUES"
                 " (7, 'e1', 'o1', ''), (7, 'e1', 'i1', ''), (7, 'e1', 'i2', ''), (7, 'e2', 'i1', '')")
    conn.commit()
    conn.close()


@pytest.fixture
def legacy_client(data_dir):
    settings = get_settings()
    settings.ensure_dirs()
    db = settings.data_dir / "processlens.db"
    command.upgrade(_alembic(settings.resolved_database_url), "0006")
    _legacy_database(db)
    from app.main import app

    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        yield c, db


def test_upgrade_converts_datasets_and_keeps_kpis(legacy_client):
    client, db = legacy_client
    assert db.with_name("processlens.db.bak-0007").exists()  # copy taken before converting
    datasets = {d["name"]: d for d in client.get("/api/datasets").json()}
    assert set(datasets) == {"legacy", "standalone"}
    legacy = datasets["legacy"]
    assert (legacy["version"], legacy["settings_revision"]) == (3, 2)  # caches and runs stay valid
    d = client.get(f"/api/datasets/{legacy['id']}/overview").json()["data"]
    assert (d["case_count"], d["event_count"], d["activity_count"], d["variant_count"]) == (5, 16, 4, 4)
    assert d["status_counts"] == {"completed": 3, "incomplete": 1, "unknown": 1}
    assert d["total_cost"] == pytest.approx(73.0)
    assert d["completed_throughput"]["median"] == pytest.approx(14400)
    assert d["open_case_age"]["max"] == pytest.approx(144000)
    case = client.get(f"/api/datasets/{legacy['id']}/cases/C1").json()
    assert [e["activity"] for e in case["events"]] == ["A", "B", "C", "D"]
    assert case["events"][0]["attributes"] == {"note": "row 2"} and case["events"][0]["event_id"] is None
    rules = client.get(f"/api/datasets/{legacy['id']}/conformance/summary").json()["rules"]
    assert rules[0]["applies"] and (rules[0]["violating"], rules[0]["compliant"]) == (2, 3)  # C4 and 007 end elsewhere

    sa = datasets["standalone"]
    assert sa["case_count"] == 1  # objects of the main type (the first declared type, order)
    items = client.get(f"/api/datasets/{sa['id']}/overview", params={"object_type": "item"}).json()["data"]
    # i1: place, pick; i2: place -> 3 object-event rows from 2 unique events
    assert (items["case_count"], items["event_count"], items["object_event_count"]) == (2, 2, 3)
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "legacy_events" in tables and "events" not in tables
