"""The 'Load demo data' action: imports the synthetic purchase-to-pay log
through the normal import pipeline and adds example rules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.i18n import t
from app.models import AlertRule, ConformanceRule, Dataset, ImportJob
from app.schemas.imports import ColumnMapping, CompletionSettings, ConfirmImportRequest, TimestampOptions
from app.services import importer

DEMO_FILES = {
    "p2p_demo_events.csv": "p2p_demo_events.csv",
    "p2p_validation_demo.csv": "p2p_validation_demo.csv",
}
def demo_request(lang: str = "en") -> ConfirmImportRequest:
    return ConfirmImportRequest(
        mapping=ColumnMapping(
            case_id="case_id", activity="activity", timestamp="timestamp", event_id="event_id",
            start_timestamp="start_timestamp", resource="resource", department="department", cost="cost",
            case_status="case_status", attributes=["vendor", "po_amount", "category"],
        ),
        timestamps=TimestampOptions(format="%Y-%m-%d %H:%M:%S", source_timezone="Europe/Berlin"),
        invalid_row_policy="reject_import",
        dataset_name=t(lang, "demo.dataset_name"),
        display_timezone="Europe/Berlin",
        completion=CompletionSettings(mode="case_status", completed_status_values=["Completed", "Cancelled"],
                                      open_status_values=["Open"]),
        reference_time=datetime(2025, 6, 27, 16, 0),  # UTC; = data extraction time 18:00 Berlin
    )


def start_demo_import(db: Session, lang: str = "en") -> ImportJob:
    path = PROJECT_ROOT / "sample_data" / DEMO_FILES["p2p_demo_events.csv"]
    upload, _ = importer.create_upload_from_path(db, path, "p2p_demo_events.csv")
    return importer.start_import(db, upload, demo_request(lang), kind="demo",
                                 extra={"demo": True, "lang": lang, "description": t(lang, "demo.description")})


def create_demo_rules(db: Session, ds: Dataset, lang: str = "en") -> None:
    """Add example conformance and alert rules once per demo dataset."""
    if db.scalar(select(func.count()).select_from(ConformanceRule).where(ConformanceRule.dataset_id == ds.id)):
        return
    rules = [
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.po_approval"), rule_type="required_activity",
                        params={"activity": "Approve Purchase Order", "assume_complete": False}),
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.approve_before_send"), rule_type="precedence",
                        params={"activity_a": "Approve Purchase Order", "activity_b": "Send Purchase Order",
                                "assume_complete": False}),
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.sla"), rule_type="max_duration",
                        params={"max_hours": 720.0, "metric": "completed_throughput"}),
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.one_price_change"), rule_type="max_occurrences",
                        params={"activity": "Change Price", "max_count": 1, "assume_complete": False}),
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.starts_requisition"), rule_type="start_activity",
                        params={"activities": ["Create Purchase Requisition"]}),
        ConformanceRule(dataset_id=ds.id, name=t(lang, "demo.rule.ends_payment"), rule_type="end_activity",
                        params={"activities": ["Pay Invoice", "Cancel Purchase Order"], "assume_complete": False}),
    ]
    db.add_all(rules)
    db.flush()
    db.add_all([
        AlertRule(dataset_id=ds.id, name=t(lang, "demo.alert.sla"), alert_type="duration_threshold",
                  params={"threshold_hours": 720.0, "metric": "completed_throughput"}, severity="high"),
        AlertRule(dataset_id=ds.id, name=t(lang, "demo.alert.old_open"), alert_type="duration_threshold",
                  params={"threshold_hours": 1080.0, "metric": "open_case_age"}, severity="medium"),
        AlertRule(dataset_id=ds.id, name=t(lang, "demo.alert.unapproved"), alert_type="conformance_violation",
                  params={}, severity="high", conformance_rule_id=rules[1].id),
    ])
    db.commit()
