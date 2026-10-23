"""object-centric datasets: every dataset is backed by the OCEL tables

* Adds the standard event fields to ``ocel_events``, the reporting event to
  ``ocel_object_attributes``, the OCEL link and per-object-type settings to
  ``datasets`` and an object type to rules, runs and reference models.
* Converts every case-centric dataset into an OCEL log with one object type
  ``case`` (event ids, order, resource, department, cost, start time and
  attributes kept; the case status becomes an attribute history of the case
  object). Version and settings revision are unchanged.
* Turns every standalone OCEL log into a dataset (its OC Petri net runs follow).
* Renames ``events`` to ``legacy_events`` (kept, no longer written).

The application copies a SQLite database file to ``*.bak-0007`` before running
this revision (app/migrate.py).

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHUNK = 5000
CASE_TYPE = "case"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(v):
    if v is None:
        return None
    return v if isinstance(v, (dict, list)) else json.loads(v)


def upgrade() -> None:
    with op.batch_alter_table("ocel_events") as b:
        b.add_column(sa.Column("start_ts", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("resource", sa.String(255), nullable=True))
        b.add_column(sa.Column("department", sa.String(255), nullable=True))
        b.add_column(sa.Column("cost", sa.Float(), nullable=True))
        b.add_column(sa.Column("source_row", sa.Integer(), nullable=True))
        b.add_column(sa.Column("id_generated", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("ocel_object_attributes") as b:
        b.add_column(sa.Column("event_id", sa.String(255), nullable=True))
    with op.batch_alter_table("datasets") as b:
        b.add_column(sa.Column("ocel_log_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("main_object_type", sa.String(255), nullable=False, server_default=CASE_TYPE))
        b.add_column(sa.Column("object_type_settings", sa.JSON(), nullable=True))
    for table in ("conformance_rules", "alert_rules", "mining_runs", "reference_models"):
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("object_type", sa.String(255), nullable=True))

    bind = op.get_bind()
    meta = sa.MetaData()
    datasets = sa.Table("datasets", meta, autoload_with=bind)
    events = sa.Table("events", meta, autoload_with=bind)
    logs = sa.Table("ocel_logs", meta, autoload_with=bind)
    oev = sa.Table("ocel_events", meta, autoload_with=bind)
    oobj = sa.Table("ocel_objects", meta, autoload_with=bind)
    oattr = sa.Table("ocel_object_attributes", meta, autoload_with=bind)
    oe2o = sa.Table("ocel_e2o", meta, autoload_with=bind)
    runs = sa.Table("mining_runs", meta, autoload_with=bind)

    standalone = [r._mapping for r in bind.execute(sa.select(logs)).all()]

    # ---------------------------------------------------------------- case-centric datasets -> OCEL
    for ds in bind.execute(sa.select(datasets)).all():
        ds = ds._mapping
        rows = bind.execute(sa.select(events).where(events.c.dataset_id == ds["id"])
                            .order_by(events.c.case_id, events.c.seq)).all()
        activities = sorted({r.activity for r in rows})
        attr_names = sorted({k for r in rows for k in (_json(r.attributes) or {})})
        has_status = any(r.case_status for r in rows)
        cases = sorted({r.case_id for r in rows})
        log_id = bind.execute(logs.insert().values(
            name=ds["name"], description=ds["description"], is_demo=ds["is_demo"], version=ds["version"],
            source_filename=ds["source_filename"], source_sha256=ds["source_sha256"],
            source_size_bytes=ds["source_size_bytes"],
            event_types=[{"name": a, "attributes": [{"name": n, "type": "string"} for n in attr_names]}
                         for a in activities],
            object_types=[{"name": CASE_TYPE, "attributes": [{"name": "status", "type": "string"}] if has_status else []}],
            counts={"events": len(rows), "objects": len(cases), "e2o": len(rows), "o2o": 0,
                    "event_types": len(activities), "object_types": 1 if rows else 0,
                    "object_attribute_values": sum(1 for r in rows if r.case_status)},
            findings=[], display_timezone=ds["display_timezone"] or "UTC",
            imported_at=ds["imported_at"] or _now(),
        )).inserted_primary_key[0]
        ev_rows, e2o_rows, attr_rows = [], [], []
        used: set[str] = set()
        for r in rows:
            generated = not r.event_id
            eid = r.event_id or f"row-{r.source_row}"
            if eid in used:  # cannot happen for validated imports; keep ids unique regardless
                eid, generated = f"row-{r.source_row}-{r.id}", True
            used.add(eid)
            ev_rows.append({"log_id": log_id, "event_id": eid, "event_type": r.activity, "ts": r.ts,
                            "position": r.source_row, "attributes": _json(r.attributes), "start_ts": r.start_ts,
                            "resource": r.resource, "department": r.department, "cost": r.cost,
                            "source_row": r.source_row, "id_generated": generated})
            e2o_rows.append({"log_id": log_id, "event_id": eid, "object_id": r.case_id, "qualifier": ""})
            if r.case_status:
                attr_rows.append({"log_id": log_id, "object_id": r.case_id, "name": "status",
                                  "value": r.case_status, "ts": r.ts, "event_id": eid})
        obj_rows = [{"log_id": log_id, "object_id": c, "object_type": CASE_TYPE} for c in cases]
        for table, data in ((oev, ev_rows), (oobj, obj_rows), (oe2o, e2o_rows), (oattr, attr_rows)):
            for i in range(0, len(data), CHUNK):
                bind.execute(table.insert(), data[i:i + CHUNK])
        bind.execute(datasets.update().where(datasets.c.id == ds["id"]).values(
            ocel_log_id=log_id, main_object_type=CASE_TYPE, object_type_settings={}))

    # ---------------------------------------------------------------- standalone OCEL logs -> datasets
    for lg in standalone:
        types = _json(lg["object_types"]) or []
        main = types[0]["name"] if types else CASE_TYPE
        counts = _json(lg["counts"]) or {}
        n_main = bind.execute(sa.select(sa.func.count()).select_from(oobj).where(
            oobj.c.log_id == lg["id"], oobj.c.object_type == main)).scalar()
        ds_id = bind.execute(datasets.insert().values(
            name=lg["name"], description=lg["description"], is_demo=lg["is_demo"], status="ready",
            version=lg["version"], settings_revision=1, source_filename=lg["source_filename"],
            source_sha256=lg["source_sha256"], source_size_bytes=lg["source_size_bytes"], upload_id=None,
            import_options={"source_format": "ocel"}, imported_at=lg["imported_at"], created_at=lg["imported_at"],
            source_row_count=counts.get("events", 0), event_count=counts.get("events", 0), case_count=n_main,
            quarantined_count=0, error_count=0, warning_count=0, display_timezone=lg["display_timezone"] or "UTC",
            completion_mode="none", completion_activities=[], completed_status_values=[], open_status_values=[],
            reference_time=None, attribute_columns=[], ocel_log_id=lg["id"], main_object_type=main,
            object_type_settings={},
        )).inserted_primary_key[0]
        bind.execute(runs.update().where(runs.c.ocel_log_id == lg["id"]).values(dataset_id=ds_id))

    op.rename_table("events", "legacy_events")


def downgrade() -> None:
    raise NotImplementedError("0007 converts the data model; restore the *.bak-0007 copy instead.")
