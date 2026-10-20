import {
  $, api, badge, bootPage, clear, currentDataset, currentDatasetId, exportLinks, filterParams, fmtDur, fmtInt, fmtNum,
  fmtTs, h, loading, noDatasetState, pageParam, pager, RESULT_LABEL, setUrl, showError, STATUS_LABEL, table,
  variantSequence,
} from "./core.js";

const root = $("#cases-root");
const detail = $("#case-detail");
const state = { page: 1, sort: "start", order: "asc", pageSize: 25 };

async function showCase(caseId) {
  const ds = currentDatasetId();
  setUrl({ extra: { case: caseId } }, { replace: true });
  for (const tr of root.querySelectorAll("tbody tr")) tr.classList.toggle("selected", tr.dataset.caseId === caseId);
  loading(detail, "Loading case…");
  let res;
  try {
    res = await api(`/api/datasets/${ds}/cases/${encodeURIComponent(caseId)}`);
  } catch (err) { return showError(detail, err); }
  const tz = res.display_timezone;
  const c = res.case;
  const anyCost = res.events.some((e) => e.cost !== null);
  clear(detail,
    h("div", { class: "card-head" }, h("h2", {}, `Case ${c.case_id}`), badge(STATUS_LABEL[c.status], c.status)),
    h("div", { class: "kpis" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Events"), h("div", { class: "value" }, fmtInt(c.n_events))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Observed duration"), h("div", { class: "value" }, fmtDur(c.observed_duration_s))),
      c.open_case_age_s !== null ? h("div", { class: "kpi" }, h("div", { class: "label" }, "Open-case age"), h("div", { class: "value" }, fmtDur(c.open_case_age_s)),
        h("div", { class: "sub" }, "at the dataset reference time")) : null,
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Repeated activities"), h("div", { class: "value" }, fmtInt(c.repeated_activities))),
      anyCost ? h("div", { class: "kpi" }, h("div", { class: "label" }, "Total cost"), h("div", { class: "value" }, fmtNum(c.total_cost, 2))) : null),
    h("h3", { style: { marginTop: "12px" } }, "Variant"), variantSequence(res.events.map((e) => e.activity)),
    res.has_timestamp_ties ? h("p", { class: "notice warn small", style: { marginTop: "10px" } },
      "Some events share a timestamp (orange markers). Their order follows the source row and does not establish which happened first.") : null,
    res.conformance.length ? [h("h3", { style: { marginTop: "12px" } }, "Conformance findings"),
      table([
        { key: "rule_name", label: "Rule", render: (r) => h("div", {}, r.rule_name, h("div", { class: "small muted" }, r.rule)) },
        { key: "result", label: "Result", render: (r) => badge(RESULT_LABEL[r.result], r.result) },
        { key: "explanation", label: "Explanation" },
      ], res.conformance)] : h("p", { class: "small muted" }, "No conformance rules defined for this dataset."),
    h("h3", { style: { marginTop: "12px" } }, `Timeline (${tz})`),
    h("ol", { class: "timeline" }, res.events.map((e) => h("li", {},
      h("span", { class: `dot ${e.tied_timestamp ? "tie" : ""}`, title: e.tied_timestamp ? "Shares its timestamp with a neighbouring event" : null }),
      h("div", {},
        e.elapsed_since_previous_s !== null ? h("div", { class: "gap-note" }, `+${fmtDur(e.elapsed_since_previous_s)} after previous event`) : null,
        h("div", { class: "ev-head" }, h("b", {}, e.activity), h("span", { class: "muted small" }, fmtTs(e.timestamp, tz, { seconds: true }))),
        h("div", { class: "ev-meta" },
          e.resource ? h("span", {}, `Resource: ${e.resource}`) : null,
          e.department ? h("span", {}, `Department: ${e.department}`) : null,
          e.cost !== null ? h("span", {}, `Cost: ${fmtNum(e.cost, 2)}`) : null,
          e.execution_s !== null ? h("span", {}, `Execution: ${fmtDur(e.execution_s)} (start ${fmtTs(e.start_timestamp, tz)})`) : null,
          e.event_id ? h("span", {}, `Event ID: ${e.event_id}`) : null,
          h("span", {}, `Source row ${e.source_row}`)),
        Object.keys(e.attributes).length ? h("div", { class: "ev-meta" }, Object.entries(e.attributes).map(([k, v]) => h("span", {}, `${k}: ${v}`))) : null)))));
}

async function render() {
  const ds = currentDatasetId();
  if (!ds) { clear($("#case-exports")); clear(detail); return noDatasetState(root); }
  loading(root, "Loading cases…");
  const params = filterParams();
  params.set("page", state.page);
  params.set("page_size", state.pageSize);
  params.set("sort", state.sort);
  params.set("order", state.order);
  let res;
  try { res = await api(`/api/datasets/${ds}/cases`, { params }); } catch (err) { return showError(root, err); }
  clear($("#case-exports"), exportLinks(ds, [["cases", "csv", "Cases CSV"], ["events", "csv", "Events CSV"], ["cases", "json", "Cases JSON"]]));
  const d = res.data;
  const tz = currentDataset()?.display_timezone || "UTC";
  if (!d.total) {
    clear(detail, h("div", { class: "state" }, "No case selected."));
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, "No cases match the current filters"),
      `The dataset has ${fmtInt(res.population.total_case_count)} cases. Remove a filter chip to widen the selection.`));
  }
  const onSort = (key) => {
    if (state.sort === key) state.order = state.order === "asc" ? "desc" : "asc";
    else { state.sort = key; state.order = key === "case_id" ? "asc" : "desc"; }
    state.page = 1;
    render();
  };
  clear(root,
    h("p", { class: "small muted" }, `${fmtInt(d.total)} of ${fmtInt(res.population.total_case_count)} cases match. Times shown in ${tz}.`),
    table([
      { key: "case_id", label: "Case ID", sortable: true, render: (c) => h("span", { class: "mono" }, c.case_id) },
      { key: "start", label: "Start", sortable: true, render: (c) => fmtTs(c.start, tz) },
      { key: "duration", label: "Observed duration", num: true, sortable: true, render: (c) => fmtDur(c.observed_duration_s) },
      { key: "events", label: "Events", num: true, sortable: true, render: (c) => fmtInt(c.n_events) },
      { key: "status", label: "Status", sortable: true, render: (c) => badge(STATUS_LABEL[c.status], c.status) },
      { key: "rework", label: "Rework", sortable: true, render: (c) => c.rework ? badge(`${c.repeated_activities}×`, "warn") : "—" },
      { key: "variant", label: "Variant", num: true, render: (c) => (c.variant_rank ? `#${c.variant_rank}` : "—") },
    ], d.items, { sort: { key: state.sort, order: state.order }, onSort, onRowClick: (c) => showCase(c.case_id) }),
    pager({ page: d.page, pageSize: d.page_size, total: d.total }, (p) => { state.page = p; render(); }));
  root.querySelectorAll("tbody tr").forEach((tr, i) => { tr.dataset.caseId = d.items[i].case_id; });
  const want = pageParam("case");
  if (want) showCase(want);
}

bootPage(() => { state.page = 1; return render(); });
