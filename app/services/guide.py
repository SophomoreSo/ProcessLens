"""Guided, question-driven analysis.

Each question maps to one deterministic backend function that reuses the
regular analytics (no language model, no free-text interpretation). Every
answer carries the metric definition, dataset version, filters, sample size,
a chart specification, a table and links to the detailed pages. A question
that the data cannot answer says so (``status: not_answerable`` plus a reason)
instead of returning an empty or zero result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConformanceRule, Dataset, OcelLog
from app.services import analytics
from app.services import ocel as ocel_svc
from app.services.conformance import NOT_EVALUABLE, VIOLATING, evaluate
from app.services.filtering import FilteredView


@dataclass(frozen=True)
class GuideParam:
    name: str
    type: str  # int | float | choice | object_type
    default: object
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    label_key: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    source: str  # dataset | ocel
    title_key: str
    description_key: str
    metric_key: str
    definition_key: str
    params: tuple[GuideParam, ...] = field(default_factory=tuple)


QUESTIONS: tuple[Question, ...] = (
    Question("longest_transitions", "dataset", "guide.q.longest_transitions", "guide.d.longest_transitions",
             "guide.m.transition_elapsed", "guide.def.transition_elapsed",
             (GuideParam("statistic", "choice", "median", choices=("median", "p90", "mean"), label_key="guide.p.statistic"),
              GuideParam("min_cases", "int", 5, 1, None, label_key="guide.p.min_cases"),
              GuideParam("limit", "int", 10, 1, 50, label_key="guide.p.limit"))),
    Question("rework_variants", "dataset", "guide.q.rework_variants", "guide.d.rework_variants",
             "guide.m.rework", "guide.def.rework",
             (GuideParam("limit", "int", 10, 1, 50, label_key="guide.p.limit"),)),
    Question("sla_violations", "dataset", "guide.q.sla_violations", "guide.d.sla_violations",
             "guide.m.sla", "guide.def.sla",
             (GuideParam("max_hours", "float", None, 0.0, None, label_key="guide.p.max_hours"),
              GuideParam("metric", "choice", "completed_throughput", choices=("completed_throughput", "observed_duration"),
                         label_key="guide.p.duration_metric"),
              GuideParam("limit", "int", 20, 1, 100, label_key="guide.p.limit"))),
    Question("department_differences", "dataset", "guide.q.department_differences", "guide.d.department_differences",
             "guide.m.participating_duration", "guide.def.participating_duration",
             (GuideParam("min_cases", "int", 5, 1, None, label_key="guide.p.min_cases"),)),
    Question("object_relationship_delays", "ocel", "guide.q.object_relationship_delays",
             "guide.d.object_relationship_delays", "guide.m.object_lifetime", "guide.def.object_lifetime",
             (GuideParam("object_type", "object_type", None, label_key="guide.p.object_type"),)),
)
BY_ID = {q.id: q for q in QUESTIONS}


class GuideError(ValueError):
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def describe() -> list[dict]:
    return [{"id": q.id, "source": q.source, "title_key": q.title_key, "description_key": q.description_key,
             "metric_key": q.metric_key, "definition_key": q.definition_key,
             "params": [{"name": p.name, "type": p.type, "default": p.default, "minimum": p.minimum,
                         "maximum": p.maximum, "choices": list(p.choices), "label_key": p.label_key} for p in q.params]}
            for q in QUESTIONS]


def validate_params(q: Question, raw: dict) -> dict:
    out = {}
    unknown = sorted(set(raw) - {p.name for p in q.params})
    if unknown:
        raise GuideError("guide.err.unknown_params", params=", ".join(unknown))
    for p in q.params:
        v = raw.get(p.name)
        if v is None or v == "":
            out[p.name] = p.default
            continue
        try:
            if p.type == "int":
                v = int(v)
            elif p.type == "float":
                v = float(v)
            elif p.type == "choice" and v not in p.choices:
                raise ValueError
            elif p.type == "object_type":
                v = str(v)
        except (TypeError, ValueError):
            raise GuideError("guide.err.invalid_param", param=p.name) from None
        if p.type == "int" and p.minimum is not None and v < p.minimum:
            raise GuideError("guide.err.invalid_param", param=p.name)
        if p.type == "float" and p.minimum is not None and v <= p.minimum:  # float minimums are exclusive
            raise GuideError("guide.err.invalid_param", param=p.name)
        if p.maximum is not None and p.type in ("int", "float") and v > p.maximum:
            raise GuideError("guide.err.invalid_param", param=p.name)
        out[p.name] = v
    return out


# --------------------------------------------------------------------------- helpers
def _query(ds: Dataset, view: FilteredView, **extra) -> str:
    """Query string that reopens a page on the same dataset and filters."""
    params = [("dataset", ds.id)]
    for k, v in view.spec.normalized().items():
        if k in extra:
            continue
        for item in v if isinstance(v, list) else [v]:
            params.append((k, str(item).lower() if isinstance(item, bool) else item))
    params += [(k, v) for k, v in extra.items() if v is not None]
    return urlencode(params)


def _dataset_provenance(ds: Dataset, view: FilteredView, n: int, n_label_key: str) -> dict:
    return {"source": "dataset", "dataset_id": ds.id, "dataset_name": ds.name, "dataset_version": ds.version,
            "settings_revision": ds.settings_revision, "filters": view.spec.normalized(),
            "population": {"cases": view.case_count, "total_cases": view.total_case_count},
            "sample": {"n": int(n), "label_key": n_label_key}}


def _answer(q: Question, params: dict, provenance: dict, *, status: str = "answered", headline_key: str = "",
            headline_params: dict | None = None, chart: dict | None = None, table: dict | None = None,
            links: list | None = None, caveats: list | None = None, reason_key: str | None = None,
            reason_params: dict | None = None) -> dict:
    return {"question_id": q.id, "title_key": q.title_key, "status": status, "params": params,
            "metric": {"name_key": q.metric_key, "definition_key": q.definition_key},
            "headline": {"key": headline_key, "params": headline_params or {}} if headline_key else None,
            "reason": {"key": reason_key, "params": reason_params or {}} if reason_key else None,
            "provenance": provenance, "chart": chart, "table": table, "links": links or [], "caveat_keys": caveats or []}


# --------------------------------------------------------------------------- dataset questions
def longest_transitions(q: Question, p: dict, ds: Dataset, view: FilteredView) -> dict:
    rows = analytics.transitions(view, min_cases=p["min_cases"], sort=p["statistic"])
    all_rows = analytics.transitions(view, min_cases=1)
    prov = _dataset_provenance(ds, view, sum(r["occurrences"] for r in rows), "guide.n.transition_occurrences")
    links = [{"label_key": "guide.link.performance", "href": f"/performance?{_query(ds, view)}"},
             {"label_key": "guide.link.process", "href": f"/process?{_query(ds, view)}"}]
    if not rows:
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_transitions",
                       reason_params={"min_cases": p["min_cases"], "available": len(all_rows)}, links=links)
    top = rows[:p["limit"]]
    stat = p["statistic"]
    label = lambda r: f"{r['source']} → {r['target']}"
    return _answer(
        q, p, prov, headline_key="guide.h.longest_transitions",
        headline_params={"transition": label(top[0]), "seconds": top[0]["elapsed"][stat], "statistic": stat,
                         "cases": top[0]["case_count"]},
        chart={"kind": "bar_h", "labels": [label(r) for r in top], "values": [r["elapsed"][stat] for r in top],
               "unit": "seconds", "value_label_key": f"guide.stat.{stat}"},
        table={"columns": [{"key": "transition", "label_key": "guide.col.transition"},
                           {"key": "median", "label_key": "guide.stat.median", "format": "duration"},
                           {"key": "p90", "label_key": "guide.stat.p90", "format": "duration"},
                           {"key": "occurrences", "label_key": "guide.col.occurrences", "format": "int"},
                           {"key": "cases", "label_key": "guide.col.cases", "format": "int"}],
               "rows": [{"transition": label(r), "median": r["elapsed"]["median"], "p90": r["elapsed"]["p90"],
                         "occurrences": r["occurrences"], "cases": r["case_count"]} for r in top]},
        links=links, caveats=["guide.c.elapsed_mixed", "guide.c.min_cases"])


def rework_variants(q: Question, p: dict, ds: Dataset, view: FilteredView) -> dict:
    items = analytics.variants(view)["items"]
    rework = [v for v in items if v["has_rework"]]
    rework.sort(key=lambda v: (-v["case_count"] * v["repeated_activities"], -v["case_count"], v["label"]))
    rework_cases = int(view.cases["rework"].sum())
    prov = _dataset_provenance(ds, view, view.case_count, "guide.n.cases")
    links = [{"label_key": "guide.link.variants", "href": f"/variants?{_query(ds, view)}"}]
    if not view.case_count:
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_cases", links=links)
    top = rework[:p["limit"]]
    share = rework_cases / view.case_count
    return _answer(
        q, p, prov,
        headline_key="guide.h.rework_variants" if top else "guide.h.no_rework",
        headline_params={"cases": rework_cases, "share": share, "variants": len(rework)},
        chart={"kind": "bar_h", "labels": [f"#{v['rank']}" for v in top],
               "values": [v["case_count"] * v["repeated_activities"] for v in top], "unit": "count",
               "value_label_key": "guide.col.repeated_executions",
               "hover": [v["label"] for v in top]} if top else None,
        table={"columns": [{"key": "rank", "label_key": "guide.col.rank", "format": "int"},
                           {"key": "variant", "label_key": "guide.col.variant"},
                           {"key": "cases", "label_key": "guide.col.cases", "format": "int"},
                           {"key": "repeated", "label_key": "guide.col.repeated_per_case", "format": "int"},
                           {"key": "repeated_total", "label_key": "guide.col.repeated_executions", "format": "int"},
                           {"key": "median", "label_key": "guide.col.median_duration", "format": "duration"}],
               "rows": [{"rank": v["rank"], "variant": v["label"], "cases": v["case_count"],
                         "repeated": v["repeated_activities"], "repeated_total": v["case_count"] * v["repeated_activities"],
                         "median": v["observed_duration"]["median"]} for v in top]},
        links=links + [{"label_key": "guide.link.rework_cases", "href": f"/cases?{_query(ds, view, rework='true')}"}],
        caveats=["guide.c.rework_definition"])


def _default_sla(db: Session, ds: Dataset) -> tuple[float | None, str | None]:
    rule = db.scalars(select(ConformanceRule).where(ConformanceRule.dataset_id == ds.id,
                                                    ConformanceRule.rule_type == "max_duration")
                      .order_by(ConformanceRule.id)).first()
    if rule is None:
        return None, None
    return float(rule.params["max_hours"]), rule.params.get("metric", "completed_throughput")


def sla_violations(q: Question, p: dict, ds: Dataset, view: FilteredView, db: Session) -> dict:
    source_key = "guide.sla_from_param"
    if p["max_hours"] is None:
        hours, metric = _default_sla(db, ds)
        if hours is None:
            prov = _dataset_provenance(ds, view, 0, "guide.n.cases")
            return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_sla",
                           links=[{"label_key": "guide.link.conformance", "href": f"/conformance?{_query(ds, view)}"}])
        p = {**p, "max_hours": hours, "metric": metric}
        source_key = "guide.sla_from_rule"
    res = evaluate(view.log, "max_duration", {"max_hours": p["max_hours"], "metric": p["metric"]})
    res = res.loc[view.cases.index]
    evaluable = res[res["result"] != NOT_EVALUABLE]
    violating = res[res["result"] == VIOLATING]
    prov = _dataset_provenance(ds, view, len(evaluable), "guide.n.evaluable_cases")
    links = [{"label_key": "guide.link.conformance", "href": f"/conformance?{_query(ds, view)}"}]
    if evaluable.empty:
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_evaluable_cases",
                       reason_params={"not_evaluable": int(len(res))}, links=links)
    dur = view.cases.loc[evaluable.index, "duration_s"]
    hist = analytics.histogram(dur)
    worst = view.cases.loc[violating.index].sort_values(["duration_s"], ascending=False).head(p["limit"])
    return _answer(
        q, p, prov, headline_key="guide.h.sla_violations",
        headline_params={"violating": int(len(violating)), "evaluable": int(len(evaluable)),
                         "share": len(violating) / len(evaluable), "hours": p["max_hours"],
                         "not_evaluable": int(len(res) - len(evaluable))},
        chart={"kind": "histogram", "edges": hist["edges"], "counts": hist["counts"], "unit": "hours",
               "threshold": p["max_hours"], "value_label_key": "guide.col.cases"},
        table={"columns": [{"key": "case_id", "label_key": "guide.col.case", "format": "case_link"},
                           {"key": "duration", "label_key": "guide.col.duration", "format": "duration"},
                           {"key": "status", "label_key": "guide.col.status", "format": "status"}],
               "rows": [{"case_id": cid, "duration": float(r["duration_s"]), "status": r["status"]}
                        for cid, r in worst.iterrows()]},
        links=links, caveats=[source_key, f"guide.c.sla_{p['metric']}"])


def department_differences(q: Question, p: dict, ds: Dataset, view: FilteredView) -> dict:
    links = [{"label_key": "guide.link.performance", "href": f"/performance?{_query(ds, view)}"}]
    if not view.log.has_department:
        prov = _dataset_provenance(ds, view, 0, "guide.n.cases")
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_department", links=links)
    depts = [d for d in analytics.performance(view)["departments"] if d["cases"] >= p["min_cases"]]
    prov = _dataset_provenance(ds, view, sum(d["cases"] for d in depts), "guide.n.case_department_pairs")
    if len(depts) < 2:
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.too_few_departments",
                       reason_params={"min_cases": p["min_cases"], "found": len(depts)}, links=links)
    depts.sort(key=lambda d: -d["participating_case_duration"]["median"])
    hi, lo = depts[0], depts[-1]
    return _answer(
        q, p, prov, headline_key="guide.h.department_differences",
        headline_params={"slowest": hi["name"], "slowest_s": hi["participating_case_duration"]["median"],
                         "fastest": lo["name"], "fastest_s": lo["participating_case_duration"]["median"],
                         "departments": len(depts)},
        chart={"kind": "bar_h", "labels": [d["name"] for d in depts],
               "values": [d["participating_case_duration"]["median"] for d in depts], "unit": "seconds",
               "value_label_key": "guide.col.median_case_duration"},
        table={"columns": [{"key": "department", "label_key": "guide.col.department"},
                           {"key": "cases", "label_key": "guide.col.cases", "format": "int"},
                           {"key": "events", "label_key": "guide.col.events", "format": "int"},
                           {"key": "median", "label_key": "guide.col.median_case_duration", "format": "duration"},
                           {"key": "p90", "label_key": "guide.stat.p90", "format": "duration"},
                           {"key": "rework", "label_key": "guide.col.rework_rate", "format": "pct"},
                           {"key": "wait", "label_key": "guide.col.median_elapsed_before", "format": "duration"}],
               "rows": [{"department": d["name"], "cases": d["cases"], "events": d["events"],
                         "median": d["participating_case_duration"]["median"],
                         "p90": d["participating_case_duration"]["p90"],
                         "rework": d["participating_case_rework_rate"],
                         "wait": d["elapsed_before_event"]["median"]} for d in depts]},
        links=links, caveats=["guide.c.participation", "guide.c.descriptive"])


# --------------------------------------------------------------------------- object-centric question
def object_relationship_delays(q: Question, p: dict, db: Session, ocel: OcelLog) -> dict:
    types = [t_["name"] for t_ in ocel.object_types]
    otype = p["object_type"] or (types[0] if types else None)
    if otype not in types:
        raise GuideError("ocel.err.unknown_object_type", types=str(otype))
    p = {**p, "object_type": otype}
    data = ocel_svc.load(db, ocel)
    r = ocel_svc.relationship_delays(data, otype)
    prov = {"source": "ocel", "ocel_log_id": ocel.id, "ocel_log_name": ocel.name, "ocel_version": ocel.version,
            "filters": {"object_type": otype}, "population": {"objects": r["objects"]},
            "sample": {"n": r["objects"], "label_key": "guide.n.objects"}}
    links = [{"label_key": "guide.link.ocel_relations", "href": f"/ocel?{urlencode({'log': ocel.id, 'tab': 'relations'})}"}]
    best = None
    rows = []
    for g in r["groups"]:
        buckets = [b for b in g["buckets"] if b["objects"] >= 3]
        if len(buckets) < 2:
            continue
        lo, hi = min(buckets, key=lambda b: b["related"]), max(buckets, key=lambda b: b["related"])
        diff = hi["median_lifetime_s"] - lo["median_lifetime_s"]
        rows.append({"relation": g["related_type"], "via": g["via"], "fewest": lo["related_label"],
                     "fewest_median": lo["median_lifetime_s"], "fewest_objects": lo["objects"],
                     "most": hi["related_label"], "most_median": hi["median_lifetime_s"], "most_objects": hi["objects"],
                     "difference": diff})
        if best is None or abs(diff) > abs(best["difference"]):
            best = rows[-1]
    if not rows:
        return _answer(q, p, prov, status="not_answerable", reason_key="guide.r.no_relation_groups",
                       reason_params={"type": otype}, links=links)
    rows.sort(key=lambda x: -abs(x["difference"]))
    return _answer(
        q, p, prov, headline_key="guide.h.object_relationship_delays",
        headline_params={"type": otype, "relation": best["relation"], "fewest": best["fewest"], "most": best["most"],
                         "fewest_s": best["fewest_median"], "most_s": best["most_median"]},
        chart={"kind": "bar_h", "labels": [x["relation"] for x in rows], "values": [x["difference"] for x in rows],
               "unit": "seconds", "value_label_key": "guide.col.lifetime_difference"},
        table={"columns": [{"key": "relation", "label_key": "guide.col.related_type"},
                           {"key": "via", "label_key": "guide.col.via", "format": "via"},
                           {"key": "fewest", "label_key": "guide.col.fewest_related"},
                           {"key": "fewest_median", "label_key": "guide.col.median_lifetime", "format": "duration"},
                           {"key": "fewest_objects", "label_key": "guide.col.objects", "format": "int"},
                           {"key": "most", "label_key": "guide.col.most_related"},
                           {"key": "most_median", "label_key": "guide.col.median_lifetime", "format": "duration"},
                           {"key": "most_objects", "label_key": "guide.col.objects", "format": "int"}],
               "rows": rows},
        links=links, caveats=["guide.c.association", "guide.c.min_group"])


def answer(db: Session, question_id: str, raw_params: dict, *, ds: Dataset | None = None,
           view: FilteredView | None = None, ocel: OcelLog | None = None) -> dict:
    q = BY_ID.get(question_id)
    if q is None:
        raise GuideError("guide.err.unknown_question", question=question_id)
    p = validate_params(q, raw_params)
    if q.source == "ocel":
        if ocel is None:
            raise GuideError("guide.err.needs_ocel")
        return object_relationship_delays(q, p, db, ocel)
    if ds is None or view is None:
        raise GuideError("guide.err.needs_dataset")
    if q.id == "longest_transitions":
        return longest_transitions(q, p, ds, view)
    if q.id == "rework_variants":
        return rework_variants(q, p, ds, view)
    if q.id == "sla_violations":
        return sla_violations(q, p, ds, view, db)
    return department_differences(q, p, ds, view)

