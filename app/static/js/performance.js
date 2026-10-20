import { cssVar, esc, onThemeChange, render as plot } from "./charts.js";
import {
  $, $$, api, bootPage, clear, currentDatasetId, exportLinks, fmtDur, fmtInt, fmtPct, getFilterOptions, h, linkWith,
  loading, noDatasetState, readFilters, showError, table,
} from "./core.js";

let perf = null;
let lastCompare = null;

function participationTable(rows, label) {
  if (!rows.length) return h("div", { class: "state" }, `No ${label.toLowerCase()} values mapped in this dataset.`);
  return table([
    { key: "name", label },
    { key: "events", label: "Events", num: true, render: (r) => fmtInt(r.events) },
    { key: "cases", label: "Cases (n)", num: true, render: (r) => fmtInt(r.cases) },
    { key: "em", label: "Median elapsed before event", num: true, render: (r) => fmtDur(r.elapsed_before_event.median) },
    { key: "ep", label: "p90", num: true, render: (r) => fmtDur(r.elapsed_before_event.p90) },
    { key: "en", label: "n", num: true, render: (r) => fmtInt(r.elapsed_before_event.n) },
    { key: "cd", label: "Median duration of their cases", num: true, render: (r) => fmtDur(r.participating_case_duration.median) },
    { key: "rw", label: "Rework rate of their cases", num: true, render: (r) => fmtPct(r.participating_case_rework_rate) },
  ], rows);
}

function drawTransitions() {
  if (!perf) return;
  const key = $("#rank-by").value;
  const rows = [...perf.transitions].sort((a, b) => key === "occurrences" ? b.occurrences - a.occurrences : b.elapsed[key] - a.elapsed[key]);
  const top = rows.slice(0, 15).reverse();
  const el = $("#chart-transitions");
  if (!top.length) clear(el, h("div", { class: "state" }, "No transitions in the selection."));
  else plot(el, [
    { type: "bar", orientation: "h", name: "median", y: top.map((t) => esc(`${t.source} → ${t.target}`)), x: top.map((t) => t.elapsed.median / 3600),
      marker: { color: cssVar("--chart-1") }, customdata: top.map((t) => t.case_count), hovertemplate: "%{y}<br>median %{x:.1f} h · %{customdata} cases<extra></extra>" },
    { type: "bar", orientation: "h", name: "p90", y: top.map((t) => esc(`${t.source} → ${t.target}`)), x: top.map((t) => t.elapsed.p90 / 3600),
      marker: { color: cssVar("--chart-2") }, hovertemplate: "%{y}<br>p90 %{x:.1f} h<extra></extra>" },
  ], { barmode: "group", showlegend: true, legend: { orientation: "h", y: 1.08 }, margin: { l: 300, r: 16, t: 24, b: 40 },
    xaxis: { title: { text: "hours" } } });
  clear($("#transitions-table"), table([
    { key: "t", label: "Transition", render: (t) => h("a", { href: linkWith("/cases", { filters: { ...readFilters(), transition_from: t.source, transition_to: t.target } }) }, `${t.source} → ${t.target}`) },
    { key: "occ", label: "Occurrences", num: true, render: (t) => fmtInt(t.occurrences) },
    { key: "cases", label: "Cases (n)", num: true, render: (t) => fmtInt(t.case_count) },
    { key: "median", label: "Median", num: true, render: (t) => fmtDur(t.elapsed.median) },
    { key: "p90", label: "p90", num: true, render: (t) => fmtDur(t.elapsed.p90) },
    { key: "mean", label: "Mean", num: true, render: (t) => fmtDur(t.elapsed.mean) },
  ], rows));
}

function cohortFields() {
  const o = getFilterOptions() || { activities: [], resources: [], departments: [] };
  for (const fs of $$("#cohort-form fieldset[data-cohort]")) {
    const box = fs.querySelector(".cohort-fields");
    const multi = (name, label, values) => h("label", {}, label, h("select", { name, multiple: true, disabled: !values.length },
      values.map((v) => h("option", { value: v }, v))));
    clear(box,
      multi("activity", "Contains activities", o.activities),
      multi("department", "Any event in department", o.departments),
      multi("resource", "Any event by resource", o.resources),
      h("label", {}, "Rework", h("select", { name: "rework" }, h("option", { value: "" }, "Any"), h("option", { value: "true" }, "Rework only"), h("option", { value: "false" }, "No rework"))),
      h("label", {}, "Min observed duration (h)", h("input", { type: "number", min: "0", step: "any", name: "min_duration_h" })),
      h("label", {}, "Max observed duration (h)", h("input", { type: "number", min: "0", step: "any", name: "max_duration_h" })));
  }
  // Dataset-agnostic default: rework cases vs. cases without rework (disjoint cohorts).
  $('fieldset[data-cohort="a"] select[name="rework"]').value = "true";
  $('fieldset[data-cohort="b"] select[name="rework"]').value = "false";
  $('fieldset[data-cohort="a"] input[name="label"]').value = "Rework cases";
  $('fieldset[data-cohort="b"] input[name="label"]').value = "No rework";
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
  ], { barmode: "overlay", showlegend: true, legend: { orientation: "h", y: 1.1 }, xaxis: { title: { text: "observed duration (hours)" } }, yaxis: { title: { text: "cases" } } });
}

async function compare(e) {
  e?.preventDefault();
  const out = $("#cohort-result");
  const A = readCohort("a");
  const B = readCohort("b");
  loading(out, "Comparing…");
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/performance/compare`, {
      method: "POST", params: new URLSearchParams(location.search), body: { a: A.spec, b: B.spec, label_a: A.label, label_b: B.label },
    });
    lastCompare = res.data;
  } catch (err) { return showError(out, err); }
  const { a, b } = lastCompare;
  const row = (label, fa, fb) => ({ label, a: fa(a), b: fb ? fb(b) : fa(b) });
  const rows = [
    row("Cases (n)", (c) => fmtInt(c.case_count)),
    row("Events per case", (c) => (c.events_per_case === null ? "—" : c.events_per_case.toFixed(1))),
    row("Median observed duration", (c) => fmtDur(c.observed_duration.median)),
    row("p90 observed duration", (c) => fmtDur(c.observed_duration.p90)),
    row("Median completed throughput", (c) => `${fmtDur(c.completed_throughput.median)} (n=${fmtInt(c.completed_throughput.n)})`),
    row("Completion rate", (c) => fmtPct(c.completion_rate)),
    row("Rework rate", (c) => fmtPct(c.rework_rate)),
    row("Variants", (c) => fmtInt(c.variant_count)),
  ];
  const acts = [...new Set([...Object.keys(a.activity_case_share), ...Object.keys(b.activity_case_share)])].sort();
  clear(out,
    a.case_count < 30 || b.case_count < 30 ? h("p", { class: "notice warn small" }, "At least one cohort has fewer than 30 cases; medians and p90 values are unstable.") : null,
    lastCompare.overlapping_cases ? h("p", { class: "notice info small" }, `${fmtInt(lastCompare.overlapping_cases)} case(s) belong to both cohorts.`) : null,
    h("div", { class: "grid-2" },
      table([{ key: "label", label: "Metric" }, { key: "a", label: a.label, num: true }, { key: "b", label: b.label, num: true }], rows),
      h("div", { id: "cohort-hist", class: "chart" })),
    h("h3", { style: { marginTop: "12px" } }, "Share of cases containing each activity"),
    table([{ key: "act", label: "Activity" },
      { key: "a", label: a.label, num: true, render: (r) => fmtPct(a.activity_case_share[r.act] ?? 0) },
      { key: "b", label: b.label, num: true, render: (r) => fmtPct(b.activity_case_share[r.act] ?? 0) },
      { key: "d", label: "Difference (pp)", num: true, render: (r) => (((a.activity_case_share[r.act] ?? 0) - (b.activity_case_share[r.act] ?? 0)) * 100).toFixed(1) }],
    acts.map((act) => ({ act }))),
    h("ul", { class: "small muted" }, lastCompare.notes.map((n) => h("li", {}, n))));
  drawCompare();
}

async function render() {
  const ds = currentDatasetId();
  const main = $("#transitions-table");
  if (!ds) { clear($("#perf-exports")); clear($("#chart-transitions")); return noDatasetState(main); }
  loading(main, "Computing performance statistics…");
  try {
    perf = (await api(`/api/datasets/${ds}/performance`, { params: new URLSearchParams(location.search) })).data;
  } catch (err) { return showError(main, err); }
  clear($("#perf-exports"), exportLinks(ds, [["transitions", "csv", "Transitions CSV"]]));
  drawTransitions();
  clear($("#resources"), participationTable(perf.resources, "Resource"));
  clear($("#departments"), participationTable(perf.departments, "Department"));
  $("#exec-card").classList.toggle("hidden", !perf.has_start_timestamps);
  clear($("#execution"), table([
    { key: "activity", label: "Activity" },
    { key: "n", label: "n", num: true, render: (r) => fmtInt(r.n) },
    { key: "median", label: "Median", num: true, render: (r) => fmtDur(r.median) },
    { key: "p90", label: "p90", num: true, render: (r) => fmtDur(r.p90) },
    { key: "mean", label: "Mean", num: true, render: (r) => fmtDur(r.mean) },
  ], perf.activity_execution), h("p", { class: "small muted" }, "Execution duration = completion − start timestamp, only for events that have both."));
  cohortFields();
  clear($("#cohort-result"));
  lastCompare = null;
}

$("#rank-by").addEventListener("change", drawTransitions);
$("#cohort-form").addEventListener("submit", compare);
onThemeChange(() => { drawTransitions(); drawCompare(); });
bootPage(render);
