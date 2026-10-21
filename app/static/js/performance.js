import { cssVar, esc, onThemeChange, render as plot } from "./charts.js";
import {
  $, $$, api, bootPage, clear, currentDatasetId, exportLinks, fmtDur, fmtInt, fmtNum, fmtPct, getFilterOptions, h, linkWith,
  loading, noDatasetState, readFilters, reveal, showError, t, table,
} from "./core.js";

let perf = null;
let lastCompare = null;

function participationTable(rows, label) {
  if (!rows.length) return h("div", { class: "state" }, t("perf.not_mapped", { field: label }));
  return table([
    { key: "name", label },
    { key: "events", label: t("common.events"), num: true, render: (r) => fmtInt(r.events) },
    { key: "cases", label: t("perf.cases_n"), num: true, render: (r) => fmtInt(r.cases) },
    { key: "em", label: t("perf.elapsed_before"), num: true, render: (r) => fmtDur(r.elapsed_before_event.median) },
    { key: "ep", label: t("stat.p90"), num: true, render: (r) => fmtDur(r.elapsed_before_event.p90) },
    { key: "en", label: "n", num: true, render: (r) => fmtInt(r.elapsed_before_event.n) },
    { key: "cd", label: t("perf.their_duration"), num: true, render: (r) => fmtDur(r.participating_case_duration.median) },
    { key: "rw", label: t("perf.their_rework"), num: true, render: (r) => fmtPct(r.participating_case_rework_rate) },
  ], rows);
}

function drawTransitions() {
  if (!perf) return;
  const key = $("#rank-by").value;
  const rows = [...perf.transitions].sort((a, b) => key === "occurrences" ? b.occurrences - a.occurrences : b.elapsed[key] - a.elapsed[key]);
  const top = rows.slice(0, 15).reverse();
  const el = $("#chart-transitions");
  if (!top.length) clear(el, h("div", { class: "state" }, t("perf.no_transitions")));
  else plot(el, [
    { type: "bar", orientation: "h", name: esc(t("stat.median")), y: top.map((tr) => esc(`${tr.source} → ${tr.target}`)), x: top.map((tr) => tr.elapsed.median / 3600),
      marker: { color: cssVar("--chart-1") }, customdata: top.map((tr) => tr.case_count),
      hovertemplate: "%{y}<br>" + esc(t("stat.median")) + " %{x:.1f} " + esc(t("chart.hours")) + " · %{customdata} " + esc(t("chart.cases")) + "<extra></extra>" },
    { type: "bar", orientation: "h", name: esc(t("stat.p90")), y: top.map((tr) => esc(`${tr.source} → ${tr.target}`)), x: top.map((tr) => tr.elapsed.p90 / 3600),
      marker: { color: cssVar("--chart-2") }, hovertemplate: "%{y}<br>" + esc(t("stat.p90")) + " %{x:.1f} " + esc(t("chart.hours")) + "<extra></extra>" },
  ], { barmode: "group", showlegend: true, legend: { orientation: "h", y: 1.08 }, margin: { l: 300, r: 16, t: 24, b: 40 },
    xaxis: { title: { text: t("chart.hours") } } });
  clear($("#transitions-table"), table([
    { key: "t", label: t("perf.transition"), render: (tr) => h("a", { href: linkWith("/cases", { filters: { ...readFilters(), transition_from: tr.source, transition_to: tr.target } }) }, `${tr.source} → ${tr.target}`) },
    { key: "occ", label: t("perf.occurrences"), num: true, render: (tr) => fmtInt(tr.occurrences) },
    { key: "cases", label: t("perf.cases_n"), num: true, render: (tr) => fmtInt(tr.case_count) },
    { key: "median", label: t("stat.median"), num: true, render: (tr) => fmtDur(tr.elapsed.median) },
    { key: "p90", label: t("stat.p90"), num: true, render: (tr) => fmtDur(tr.elapsed.p90) },
    { key: "mean", label: t("stat.mean"), num: true, render: (tr) => fmtDur(tr.elapsed.mean) },
  ], rows));
}

function cohortFields() {
  const o = getFilterOptions() || { activities: [], resources: [], departments: [] };
  for (const fs of $$("#cohort-form fieldset[data-cohort]")) {
    const box = fs.querySelector(".cohort-fields");
    const multi = (name, label, values) => h("label", {}, label, h("select", { name, multiple: true, disabled: !values.length },
      values.map((v) => h("option", { value: v }, v))));
    clear(box,
      multi("activity", t("perf.cohort.activities"), o.activities),
      multi("department", t("filters.department_any"), o.departments),
      multi("resource", t("filters.resource_any"), o.resources),
      h("label", {}, t("filters.rework"), h("select", { name: "rework" }, h("option", { value: "" }, t("filters.any")), h("option", { value: "true" }, t("filters.rework_only")), h("option", { value: "false" }, t("filters.no_rework")))),
      h("label", {}, t("filters.min_duration"), h("input", { type: "number", min: "0", step: "any", name: "min_duration_h" })),
      h("label", {}, t("filters.max_duration"), h("input", { type: "number", min: "0", step: "any", name: "max_duration_h" })));
  }
  // Dataset-agnostic default: rework cases vs. cases without rework (disjoint cohorts).
  $('fieldset[data-cohort="a"] select[name="rework"]').value = "true";
  $('fieldset[data-cohort="b"] select[name="rework"]').value = "false";
  $('fieldset[data-cohort="a"] input[name="label"]').value = t("perf.cohort.rework");
  $('fieldset[data-cohort="b"] input[name="label"]').value = t("perf.cohort.no_rework");
}

function readCohort(key) {
  const fs = $(`fieldset[data-cohort="${key}"]`);
  const spec = {};
  for (const sel of fs.querySelectorAll("select[multiple]")) {
    const v = [...sel.selectedOptions].map((x) => x.value);
    if (v.length) spec[sel.name] = v;
  }
  const rw = fs.querySelector('select[name="rework"]').value;
  if (rw) spec.rework = rw === "true";
  for (const n of ["min_duration_h", "max_duration_h"]) {
    const v = fs.querySelector(`input[name="${n}"]`).value;
    if (v !== "") spec[n] = Number(v);
  }
  return { spec, label: fs.querySelector('input[name="label"]').value || key.toUpperCase() };
}

function drawCompare() {
  if (!lastCompare) return;
  const { a, b } = lastCompare;
  const el = $("#cohort-hist");
  if (!el) return;
  const centers = (hst) => hst.counts.map((_, i) => (hst.edges[i] + hst.edges[i + 1]) / 2);
  plot(el, [
    { type: "bar", name: esc(a.label), x: centers(a.duration_histogram), y: a.duration_histogram.counts, marker: { color: cssVar("--chart-1") }, opacity: 0.75 },
    { type: "bar", name: esc(b.label), x: centers(b.duration_histogram), y: b.duration_histogram.counts, marker: { color: cssVar("--chart-3") }, opacity: 0.75 },
  ], { barmode: "overlay", showlegend: true, legend: { orientation: "h", y: 1.1 }, xaxis: { title: { text: t("ov.duration_hours") } }, yaxis: { title: { text: t("chart.cases") } } });
}

async function compare(e) {
  e?.preventDefault();
  const out = $("#cohort-result");
  const A = readCohort("a");
  const B = readCohort("b");
  loading(out, t("perf.comparing"));
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/performance/compare`, {
      method: "POST", params: new URLSearchParams(location.search), body: { a: A.spec, b: B.spec, label_a: A.label, label_b: B.label },
    });
    lastCompare = res.data;
  } catch (err) { return showError(out, err); }
  const { a, b } = lastCompare;
  const row = (label, fa, fb) => ({ label, a: fa(a), b: fb ? fb(b) : fa(b) });
  const rows = [
    row(t("perf.cases_n"), (c) => fmtInt(c.case_count)),
    row(t("perf.m.events_per_case"), (c) => fmtNum(c.events_per_case)),
    row(t("perf.m.median_observed"), (c) => fmtDur(c.observed_duration.median)),
    row(t("perf.m.p90_observed"), (c) => fmtDur(c.observed_duration.p90)),
    row(t("perf.m.median_throughput"), (c) => `${fmtDur(c.completed_throughput.median)} (n=${fmtInt(c.completed_throughput.n)})`),
    row(t("perf.m.completion_rate"), (c) => fmtPct(c.completion_rate)),
    row(t("ov.kpi.rework_rate"), (c) => fmtPct(c.rework_rate)),
    row(t("ov.kpi.variants"), (c) => fmtInt(c.variant_count)),
  ];
  const acts = [...new Set([...Object.keys(a.activity_case_share), ...Object.keys(b.activity_case_share)])].sort();
  clear(out,
    a.case_count < 30 || b.case_count < 30 ? h("p", { class: "notice warn small" }, t("perf.small_cohort")) : null,
    lastCompare.overlapping_cases ? h("p", { class: "notice info small" }, t("perf.overlap", { n: fmtInt(lastCompare.overlapping_cases) })) : null,
    h("div", { class: "grid-2" },
      table([{ key: "label", label: t("perf.metric") }, { key: "a", label: a.label, num: true }, { key: "b", label: b.label, num: true }], rows),
      h("div", { id: "cohort-hist", class: "chart" })),
    h("h3", { style: { marginTop: "12px" } }, t("perf.activity_share")),
    table([{ key: "act", label: t("field.activity") },
      { key: "a", label: a.label, num: true, render: (r) => fmtPct(a.activity_case_share[r.act] ?? 0) },
      { key: "b", label: b.label, num: true, render: (r) => fmtPct(b.activity_case_share[r.act] ?? 0) },
      { key: "d", label: t("perf.difference_pp"), num: true, render: (r) => fmtNum(((a.activity_case_share[r.act] ?? 0) - (b.activity_case_share[r.act] ?? 0)) * 100) }],
    acts.map((act) => ({ act }))),
    h("ul", { class: "small muted" }, lastCompare.notes.map((n) => h("li", {}, n))));
  reveal(out);
  drawCompare();
}

async function render() {
  const ds = currentDatasetId();
  const main = $("#transitions-table");
  if (!ds) { clear($("#perf-exports")); clear($("#chart-transitions")); return noDatasetState(main); }
  loading(main, t("perf.computing"));
  try {
    perf = (await api(`/api/datasets/${ds}/performance`, { params: new URLSearchParams(location.search) })).data;
  } catch (err) { return showError(main, err); }
  clear($("#perf-exports"), exportLinks(ds, [["transitions", "csv", t("export.transitions_csv")]]));
  drawTransitions();
  clear($("#resources"), participationTable(perf.resources, t("field.resource")));
  clear($("#departments"), participationTable(perf.departments, t("field.department")));
  $("#exec-card").classList.toggle("hidden", !perf.has_start_timestamps);
  clear($("#execution"), table([
    { key: "activity", label: t("field.activity") },
    { key: "n", label: "n", num: true, render: (r) => fmtInt(r.n) },
    { key: "median", label: t("stat.median"), num: true, render: (r) => fmtDur(r.median) },
    { key: "p90", label: t("stat.p90"), num: true, render: (r) => fmtDur(r.p90) },
    { key: "mean", label: t("stat.mean"), num: true, render: (r) => fmtDur(r.mean) },
  ], perf.activity_execution), h("p", { class: "small muted" }, t("perf.execution_note")));
  cohortFields();
  clear($("#cohort-result"));
  lastCompare = null;
}

$("#rank-by").addEventListener("change", drawTransitions);
$("#cohort-form").addEventListener("submit", compare);
onThemeChange(() => { drawTransitions(); drawCompare(); });
bootPage(render);
