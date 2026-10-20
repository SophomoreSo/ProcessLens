"""On-demand alert evaluation (no scheduler, no external notifications).

Each alert is keyed by (alert rule, case) with a database unique constraint,
so re-running an evaluation updates existing alerts instead of duplicating
them. The user-managed ``status`` (open/acknowledged/resolved) is never reset
by an evaluation; ``condition_active`` records whether the condition still held
at the latest run.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, AlertRule, ConformanceRule, Dataset
from app.models.dataset import utcnow
from app.services.conformance import describe_rule, evaluate_rule
from app.services.eventlog import EventLog, get_event_log


def _hours(s: float) -> str:
    return f"{s / 3600:.1f} h"


def matching_cases(db: Session, log: EventLog, rule: AlertRule) -> dict[str, str]:
    """case_id -> message for every case that currently meets the alert condition."""
    cases = log.cases
    p = rule.params or {}
    out: dict[str, str] = {}
    if rule.alert_type == "duration_threshold":
        limit = float(p["threshold_hours"]) * 3600
        metric = p.get("metric", "observed_duration")
        if metric == "completed_throughput":
            sel = cases[(cases["status"] == "completed") & (cases["duration_s"] > limit)]
            for cid, d in sel["duration_s"].items():
                out[cid] = f"Completed throughput {_hours(d)} exceeds {_hours(limit)}."
        elif metric == "open_case_age":
            sel = cases[(cases["status"] != "completed") & (cases["age_s"] > limit)]
            ref = log.reference_time.isoformat() if log.reference_time is not None else "n/a"
            for cid, a in sel["age_s"].items():
                out[cid] = f"Open case age {_hours(a)} at reference time {ref} exceeds {_hours(limit)}."
        else:
            sel = cases[cases["duration_s"] > limit]
            for cid, d in sel["duration_s"].items():
                out[cid] = f"Observed duration {_hours(d)} exceeds {_hours(limit)}."
    elif rule.alert_type == "rework":
        act = p.get("activity")
        if act:
            ev = log.events
            counts = ev[ev["activity"] == act].groupby("case_id").size()
            for cid, k in counts[counts > 1].items():
                out[cid] = f"'{act}' occurs {int(k)} times (repeated {int(k) - 1}×)."
        else:
            for cid, k in cases.loc[cases["rework"], "repeated"].items():
                out[cid] = f"{int(k)} repeated activity occurrence(s) detected."
    elif rule.alert_type == "conformance_violation":
        crule = db.get(ConformanceRule, rule.conformance_rule_id)
        if crule is None or crule.dataset_id != log.dataset_id:
            return {}
        res = evaluate_rule(log, crule)
        viol = res[res["result"] == "violating"]
        for cid, e in viol["explanation"].items():
            out[cid] = f"Violates '{crule.name}' ({describe_rule(crule.rule_type, crule.params)}): {e}"
    return out


def evaluate_alert_rules(db: Session, dataset: Dataset, rule_ids: list[int] | None = None) -> dict:
    log = get_event_log(db, dataset)
    q = select(AlertRule).where(AlertRule.dataset_id == dataset.id, AlertRule.enabled.is_(True))
    if rule_ids:
        q = q.where(AlertRule.id.in_(rule_ids))
    rules = db.scalars(q).all()
    now = utcnow()
    created = updated = gone = 0
    for rule in rules:
        matches = matching_cases(db, log, rule)
        existing = {a.case_id: a for a in db.scalars(select(Alert).where(Alert.alert_rule_id == rule.id))}
        for cid, msg in matches.items():
            alert = existing.get(cid)
            if alert is None:
                db.add(Alert(alert_rule_id=rule.id, dataset_id=dataset.id, case_id=cid, status="open",
                             severity=rule.severity, message=msg, condition_active=True,
                             dataset_version=dataset.version, first_detected_at=now, last_evaluated_at=now))
                created += 1
            else:
                alert.message = msg
                alert.condition_active = True
                alert.severity = rule.severity
                alert.dataset_version = dataset.version
                alert.last_evaluated_at = now
                updated += 1
        for cid, alert in existing.items():
            if cid not in matches and alert.condition_active:
                alert.condition_active = False
                alert.last_evaluated_at = now
                gone += 1
        rule.last_evaluated_at = now
    db.commit()
    return {"rules_evaluated": len(rules), "created": created, "updated": updated,
            "no_longer_matching": gone, "evaluated_at": now.isoformat()}

