from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import build_view, filter_params, get_dataset, request_lang
from app.database import get_db
from app.errors import not_found
from app.models import Alert, AlertRule, ConformanceRule, Dataset
from app.schemas.filters import FilterSpec
from app.schemas.rules import ConformanceRuleIn, ConformanceRuleOut
from app.i18n import t
from app.services.conformance import describe_rule, evaluate_rule, explain, summarize
from app.services.eventlog import get_event_log

router = APIRouter(prefix="/api/datasets/{dataset_id}/conformance-rules", tags=["conformance"])


def _out(rule: ConformanceRule, lang: str) -> ConformanceRuleOut:
    out = ConformanceRuleOut.model_validate(rule)
    out.description = describe_rule(rule.rule_type, rule.params, lang)
    return out


def _rule(db: Session, ds: Dataset, rule_id: int) -> ConformanceRule:
    rule = db.get(ConformanceRule, rule_id)
    if rule is None or rule.dataset_id != ds.id:
        raise not_found("conformance_rule")
    return rule


@router.get("", response_model=list[ConformanceRuleOut], summary="List conformance rules")
def list_rules(ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    rules = db.scalars(select(ConformanceRule).where(ConformanceRule.dataset_id == ds.id)
                       .order_by(ConformanceRule.id)).all()
    return [_out(r, lang) for r in rules]


@router.post("", response_model=ConformanceRuleOut, status_code=201, summary="Create a conformance rule")
def create_rule(body: ConformanceRuleIn, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db),
                lang: str = Depends(request_lang)):
    rule = ConformanceRule(dataset_id=ds.id, **body.model_dump())
    db.add(rule)
    db.commit()
    return _out(rule, lang)


@router.get("/{rule_id}", response_model=ConformanceRuleOut, summary="Get a conformance rule")
def get_rule(rule_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db),
             lang: str = Depends(request_lang)):
    return _out(_rule(db, ds, rule_id), lang)


@router.put("/{rule_id}", response_model=ConformanceRuleOut, summary="Replace a conformance rule")
def update_rule(rule_id: int, body: ConformanceRuleIn, ds: Dataset = Depends(get_dataset),
                db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    rule = _rule(db, ds, rule_id)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    rule.revision += 1  # new revision => fresh cached evaluation
    db.commit()
    return _out(rule, lang)


@router.delete("/{rule_id}", status_code=204, summary="Delete a conformance rule (and alert rules using it)")
def delete_rule(rule_id: int, ds: Dataset = Depends(get_dataset), db: Session = Depends(get_db)):
    rule = _rule(db, ds, rule_id)
    alert_rule_ids = db.scalars(select(AlertRule.id).where(AlertRule.conformance_rule_id == rule.id)).all()
    if alert_rule_ids:
        db.query(Alert).filter(Alert.alert_rule_id.in_(alert_rule_ids)).delete(synchronize_session=False)
        db.query(AlertRule).filter(AlertRule.id.in_(alert_rule_ids)).delete(synchronize_session=False)
    db.delete(rule)
    db.commit()
    return Response(status_code=204)


@router.get("/{rule_id}/evaluation",
            summary="Evaluate a rule over the filtered population (compliant / violating / not evaluable)")
def evaluation(rule_id: int, ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
               result: list[str] = Query(default=[], description="Restrict listed cases to these results"),
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
               db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    rule = _rule(db, ds, rule_id)
    _, view = build_view(db, ds, spec)
    res = evaluate_rule(get_event_log(db, ds), rule).reindex(view.cases.index)
    summary = summarize(res, view.cases.index)
    listed = res[res["result"].isin(result)] if result else res
    order = {"violating": 0, "not_evaluable": 1, "compliant": 2}
    listed = listed.assign(_o=listed["result"].map(order)).sort_values(["_o"], kind="mergesort")
    total = len(listed)
    chunk = listed.iloc[(page - 1) * page_size: page * page_size]
    items = [{"case_id": cid, "result": r["result"], "explanation": explain(r, lang),
              "status": view.cases.at[cid, "status"]} for cid, r in chunk.iterrows()]
    return {"dataset_id": ds.id, "dataset_version": ds.version, "filters": spec.normalized(),
            "rule": _out(rule, lang).model_dump(mode="json"), "summary": summary,
            "cases": {"items": items, "total": total, "page": page, "page_size": page_size},
            "notes": [t(lang, "note.conf.rule_based"), t(lang, "note.conf.not_evaluable")]}


summary_router = APIRouter(prefix="/api/datasets/{dataset_id}/conformance", tags=["conformance"])


@summary_router.get("/summary", summary="Result counts for every enabled rule over the filtered population")
def all_rules_summary(ds: Dataset = Depends(get_dataset), spec: FilterSpec = Depends(filter_params),
                      db: Session = Depends(get_db), lang: str = Depends(request_lang)):
    log, view = build_view(db, ds, spec)
    out = []
    for rule in db.scalars(select(ConformanceRule).where(ConformanceRule.dataset_id == ds.id)
                           .order_by(ConformanceRule.id)):
        entry = {"rule": _out(rule, lang).model_dump(mode="json")}
        if rule.enabled:
            entry.update(summarize(evaluate_rule(log, rule), view.cases.index))
        out.append(entry)
    return {"dataset_id": ds.id, "filters": spec.normalized(), "population_case_count": view.case_count,
            "rules": out}
