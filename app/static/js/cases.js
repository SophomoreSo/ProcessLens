import {
  $, api, badge, bootPage, clear, currentDataset, currentDatasetId, exportLinks, filterParams, fmtDur,
  fmtInt, fmtNum, perspectiveLink,
  fmtTs, h, loading, noDatasetState, pageParam, pager, RESULT_LABEL, reveal, setUrl, showError, STATUS_LABEL, t, table,
  variantSequence,
} from "./core.js";

const root = $("#cases-root");
const detail = $("#case-detail");
const state = { page: 1, sort: "start", order: "asc", pageSize: 25 };

async function showCase(caseId) {
  const ds = currentDatasetId();
  setUrl({ extra: { case: caseId } }, { replace: true });
  for (const tr of root.querySelectorAll("tbody tr")) tr.classList.toggle("selected", tr.dataset.caseId === caseId);
  loading(detail, t("cs.loading_case"));
  let res;
  try {
    res = await api(`/api/datasets/${ds}/cases/${encodeURIComponent(caseId)}`, { params: filterParams({}) });
  } catch (err) { return showError(detail, err); }
  const tz = res.display_timezone;
  const c = res.case;
  const anyCost = res.events.some((e) => e.cost !== null);
  clear(detail,
    h("div", { class: "card-head" }, h("h2", {}, t("cs.object_title", { id: c.case_id, type: res.object_type })),
      badge(STATUS_LABEL[c.status], c.status)),
    h("div", { class: "kpis" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("common.events")), h("div", { class: "value" }, fmtInt(c.n_events))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("common.observed_duration")), h("div", { class: "value" }, fmtDur(c.observed_duration_s))),
      c.open_case_age_s !== null ? h("div", { class: "kpi" }, h("div", { class: "label" }, t("cs.open_age")), h("div", { class: "value" }, fmtDur(c.open_case_age_s)),
        h("div", { class: "sub" }, t("cs.at_reference"))) : null,
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("cs.repeated")), h("div", { class: "value" }, fmtInt(c.repeated_activities))),
      anyCost ? h("div", { class: "kpi" }, h("div", { class: "label" }, t("cs.total_cost")), h("div", { class: "value" }, fmtNum(c.total_cost, 2))) : null),
    h("h3", { style: { marginTop: "12px" } }, t("chip.variant")), variantSequence(res.events.map((e) => e.activity)),
    res.has_timestamp_ties ? h("p", { class: "notice warn small", style: { marginTop: "10px" } }, t("cs.ties_note")) : null,
    res.conformance.length ? [h("h3", { style: { marginTop: "12px" } }, t("cs.conformance")),
      table([
        { key: "rule_name", label: t("cf.rule"), render: (r) => h("div", {}, r.rule_name, h("div", { class: "small muted" }, r.rule)) },
        { key: "result", label: t("cf.result"), render: (r) => badge(RESULT_LABEL[r.result], r.result) },
        { key: "explanation", label: t("cf.explanation") },
      ], res.conformance)] : h("p", { class: "small muted" }, t("cs.no_rules")),
    h("h3", { style: { marginTop: "12px" } }, t("cs.timeline", { tz })),
    h("ol", { class: "timeline" }, res.events.map((e, i) => h("li", { style: { "--i": String(Math.min(i, 20)) } },
      h("span", { class: `dot ${e.tied_timestamp ? "tie" : ""}`, title: e.tied_timestamp ? t("cs.tie_title") : null }),
      h("div", {},
        e.elapsed_since_previous_s !== null ? h("div", { class: "gap-note" }, t("cs.after_previous", { duration: fmtDur(e.elapsed_since_previous_s) })) : null,
        h("div", { class: "ev-head" }, h("b", {}, e.activity), h("span", { class: "muted small" }, fmtTs(e.timestamp, tz, { seconds: true }))),
        h("div", { class: "ev-meta" },
          e.resource ? h("span", {}, t("cs.ev.resource", { value: e.resource })) : null,
          e.department ? h("span", {}, t("cs.ev.department", { value: e.department })) : null,
          e.cost !== null ? h("span", {}, t("cs.ev.cost", { value: fmtNum(e.cost, 2) })) : null,
          e.execution_s !== null ? h("span", {}, t("cs.ev.execution", { duration: fmtDur(e.execution_s), start: fmtTs(e.start_timestamp, tz) })) : null,
          e.event_id ? h("span", {}, t("cs.ev.event_id", { value: e.event_id })) : null,
          h("span", {}, t("cs.ev.source_row", { row: e.source_row }))),
        e.other_objects.length ? h("div", { class: "ev-meta" }, h("span", { class: "muted" }, t("cs.ev.with_objects")),
          e.other_objects.slice(0, 12).map((o) => h("a", { class: "obj-chip", href: perspectiveLink("/cases", o.object_type, { case: o.object_id }),
            title: o.qualifier || o.object_type }, `${o.object_id} `, h("span", { class: "muted" }, o.object_type))),
          e.other_objects.length > 12 ? h("span", { class: "muted" }, t("cs.ev.more_objects", { n: fmtInt(e.other_objects.length - 12) })) : null) : null,
        Object.keys(e.attributes).length ? h("div", { class: "ev-meta" }, Object.entries(e.attributes).map(([k, v]) => h("span", {}, `${k}: ${v}`))) : null)))),
    h("div", { id: "object-context" }));
  loadObjectContext(ds, c.case_id, tz);
}

/** Attribute history and related objects (other types too), each a link that switches the perspective. */
async function loadObjectContext(ds, objectId, tz) {
  const box = $("#object-context");
  try {
    const o = await api(`/api/datasets/${ds}/ocel/objects/${encodeURIComponent(objectId)}`);
    const link = (r) => h("a", { href: perspectiveLink("/cases", r.object_type, { case: r.object_id }) }, r.object_id);
    clear(box,
      o.attributes.length ? [h("h3", { style: { marginTop: "12px" } }, t("ocel.attribute_history")), table([
        { key: "name", label: t("ocel.attribute") },
        { key: "value", label: t("ocel.value"), render: (a) => a.value ?? "—" },
        { key: "valid_from", label: t("ocel.valid_from"), render: (a) => (a.valid_from ? fmtTs(a.valid_from, tz, { seconds: true }) : t("ocel.static")) },
      ], o.attributes)] : null,
      h("h3", { style: { marginTop: "12px" } }, t("cs.related_objects")),
      o.related_via_o2o.length || o.related_via_events.length ? table([
        { key: "o", label: t("ocel.object"), render: link },
        { key: "ty", label: t("ocel.object_type"), render: (r) => r.object_type },
        { key: "via", label: t("ocel.direction"), render: (r) => (r.qualifier !== undefined
          ? `${r.direction === "out" ? "→" : "←"} ${r.qualifier || "—"}` : t("cs.shared_events_n", { n: fmtInt(r.shared_events) })) },
      ], [...o.related_via_o2o, ...o.related_via_events]) : h("p", { class: "small muted" }, t("ocel.no_relations")));
  } catch (err) { showError(box, err); }
}

async function render() {
  const ds = currentDatasetId();
  if (!ds) { clear($("#case-exports")); clear(detail); return noDatasetState(root); }
  loading(root, t("cs.loading"));
  const params = filterParams();
  params.set("page", state.page);
  params.set("page_size", state.pageSize);
  params.set("sort", state.sort);
  params.set("order", state.order);
  let res;
  try { res = await api(`/api/datasets/${ds}/cases`, { params }); } catch (err) { return showError(root, err); }
  clear($("#case-exports"), exportLinks(ds, [["cases", "csv", t("export.cases_csv")], ["events", "csv", t("export.events_csv")], ["cases", "json", t("export.cases_json")]]));
  const d = res.data;
  const tz = currentDataset()?.display_timezone || "UTC";
  if (!d.total) {
    clear(detail, h("div", { class: "state" }, t("cs.none_selected")));
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, t("empty.no_match.title")),
      t("empty.no_match.hint_total", { total: fmtInt(res.population.total_case_count) })));
  }
  const onSort = (key) => {
    if (state.sort === key) state.order = state.order === "asc" ? "desc" : "asc";
    else { state.sort = key; state.order = key === "case_id" ? "asc" : "desc"; }
    state.page = 1;
    render();
  };
  clear(root,
    h("p", { class: "small muted" }, t("cs.summary", { n: fmtInt(d.total), total: fmtInt(res.population.total_case_count), tz })),
    table([
      { key: "case_id", label: t("field.case_id"), sortable: true, render: (c) => h("span", { class: "mono" }, c.case_id) },
      { key: "start", label: t("cs.col.start"), sortable: true, render: (c) => fmtTs(c.start, tz) },
      { key: "duration", label: t("common.observed_duration"), num: true, sortable: true, render: (c) => fmtDur(c.observed_duration_s) },
      { key: "events", label: t("common.events"), num: true, sortable: true, render: (c) => fmtInt(c.n_events) },
      { key: "status", label: t("common.status"), sortable: true, render: (c) => badge(STATUS_LABEL[c.status], c.status) },
      { key: "rework", label: t("common.rework"), sortable: true, render: (c) => c.rework ? badge(`${c.repeated_activities}×`, "warn") : "—" },
      { key: "variant", label: t("chip.variant"), num: true, render: (c) => (c.variant_rank ? `#${c.variant_rank}` : "—") },
    ], d.items, { sort: { key: state.sort, order: state.order }, onSort, onRowClick: (c) => showCase(c.case_id) }),
    pager({ page: d.page, pageSize: d.page_size, total: d.total }, (p) => { state.page = p; render(); }));
  root.querySelectorAll("tbody tr").forEach((tr, i) => { tr.dataset.caseId = d.items[i].case_id; });
  const want = pageParam("case");
  if (want) showCase(want);
}

bootPage(() => { state.page = 1; return render(); });
