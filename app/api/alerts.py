from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_dataset
from app.database import get_db
from app.errors import AppError, not_found
from app.models import Alert, AlertRule, ConformanceRule, Dataset
from app.models.dataset import utcnow
from app.schemas.common import Page
from app.schemas.rules import AlertEvaluationResult, AlertOut, AlertRuleIn, AlertRuleOut, AlertStatusUpdate
from app.services.alerts import evaluate_alert_rules

router = APIRouter(prefix="/api", tags=["alerts"])


def _alert_rule(db: Session, ds: Dataset, rule_id: int) -> AlertRule:
    rule = db.get(AlertRule, rule_id)
    if rule is None or rule.dataset_id != ds.id:
        raise not_found("Alert rule")
    return rule


def _check_conformance_ref(db: Session, ds: Dataset, body: AlertRuleIn) -> None:
    if body.conformance_rule_id is not None:
        cr = db.get(ConformanceRule, body.conformance_rule_id)
        if cr is None or cr.dataset_id != ds.id:
            raise AppError(422, "invalid_reference", "conformance_rule_id does not belong to this dataset")


@router.get("/datasets/{dataset_id}/alert-rules", response_model=list[AlertRuleOut], summary="List alert rules")
def list_alert_rules(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    return db.scalars(select(AlertRule).where(AlertRule.dataset_id == ds.id).order_by(AlertRule.id)).all()


@router.post("/datasets/{dataset_id}/alert-rules", response_model=AlertRuleOut, status_code=201,
             summary="Create an alert rule")
def create_alert_rule(body: AlertRuleIn, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    _check_conformance_ref(db, ds, body)
    rule = AlertRule(dataset_id=ds.id, **body.model_dump())
    db.add(rule)
    db.commit()
    return rule


@router.put("/datasets/{dataset_id}/alert-rules/{rule_id}", response_model=AlertRuleOut,
            summary="Replace an alert rule")
def update_alert_rule(rule_id: int, body: AlertRuleIn, ds: Dataset = Depends(get_dataset),
                      db: Session = Depends(get_db)):
    rule = _alert_rule(db, ds, rule_id)
    _check_conformance_ref(db, ds, body)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    db.commit()
    return rule


@router.delete("/datasets/{dataset_id}/alert-rules/{rule_id}", status_code=204,
               summary="Delete an alert rule and its alerts")
def delete_alert_rule(rule_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    rule = _alert_rule(db, ds, rule_id)
    db.query(Alert).filter(Alert.alert_rule_id == rule.id).delete(synchronize_session=False)
    db.delete(rule)
    db.commit()
    return Response(status_code=204)


@router.post("/datasets/{dataset_id}/alert-rules/evaluate", response_model=AlertEvaluationResult,
             summary="Evaluate enabled alert rules now (idempotent; no background monitoring)")
def evaluate(ds: Dataset = Depends(get_dataset), rule_id: list[int] = Query(default=[]),
             db: Session = Depends(get_db)):
    return evaluate_alert_rules(db, ds, rule_id or None)


@router.get("/alerts", response_model=Page[AlertOut], summary="List alerts")
def list_alerts(dataset_id: int | None = None, status: list[str] = Query(default=[]),
                alert_rule_id: int | None = None, active_only: bool = False,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                db: Session = Depends(get_db)):
    q = select(Alert, AlertRule.name).join(AlertRule, AlertRule.id == Alert.alert_rule_id)
    if dataset_id is not None:
        q = q.where(Alert.dataset_id == dataset_id)
    if status:
        q = q.where(Alert.status.in_(status))
    if alert_rule_id is not None:
        q = q.where(Alert.alert_rule_id == alert_rule_id)
    if active_only:
        q = q.where(Alert.condition_active.is_(True))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(Alert.first_detected_at.desc(), Alert.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for alert, name in rows:
        out = AlertOut.model_validate(alert)
        out.rule_name = name
        items.append(out)
    return Page[AlertOut](items=items, total=total or 0, page=page, page_size=page_size)


@router.get("/alerts/counts", summary="Alert counts by status")
def alert_counts(dataset_id: int | None = None, db: Session = Depends(get_db)):
    q = select(Alert.status, func.count()).group_by(Alert.status)
    if dataset_id is not None:
        q = q.where(Alert.dataset_id == dataset_id)
    counts = dict(db.execute(q).all())
    return {s: int(counts.get(s, 0)) for s in ("open", "acknowledged", "resolved")}


@router.patch("/alerts/{alert_id}", response_model=AlertOut, summary="Change alert status")
def update_alert(alert_id: int, body: AlertStatusUpdate, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise not_found("Alert")
    now = utcnow()
    alert.status = body.status
    if body.status == "acknowledged":
        alert.acknowledged_at = alert.acknowledged_at or now
        alert.resolved_at = None
    elif body.status == "resolved":
        alert.resolved_at = now
    else:
        alert.acknowledged_at = None
        alert.resolved_at = None
    if body.note is not None:
        alert.note = body.note
    db.commit()
    out = AlertOut.model_validate(alert)
    rule = db.get(AlertRule, alert.alert_rule_id)
    out.rule_name = rule.name if rule else None
    return out
