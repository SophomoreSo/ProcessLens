import { cssVar, esc, histogramBars, onThemeChange, render as plot } from "./charts.js";
import {
  $, api, badge, bootPage, clear, currentDatasetId, exportLinks, fmtDur, fmtInt, fmtPct, h, linkWith, loading,
  noDatasetState, readFilters, showError, table, variantSequence,
} from "./core.js";

const root = $("#overview-root");
let last = null;
let distMode = "completed_throughput";

function kpi(label, value, sub, title) {
  return h("div", { class: "kpi", title }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null);
}

function durationTable(d) {
  const rows = [
    { pop: "Observed duration — all selected cases", s: d.observed_duration,
      note: "last − first event timestamp; includes cases that may still be running" },
    { pop: "Completed throughput — completed cases only", s: d.completed_throughput,
      note: d.completion_mode === "none" ? "no completion rule configured: no case is known to be completed" : "cases completed per the dataset's completion rule" },
    { pop: "Open-case age — incomplete & unknown cases", s: d.open_case_age,
      note: `reference time − first event (${d.reference_time_source === "configured" ? "configured reference time" : "reference = latest event in dataset"})` },
  ];
  return table([
    { key: "pop", label: "Population", render: (r) => h("div", {}, r.pop, h("div", { class: "small muted" }, r.note)) },
    { key: "n", label: "Cases (n)", num: true, render: (r) => fmtInt(r.s.n) },
    { key: "mean", label: "Mean", num: true, render: (r) => fmtDur(r.s.mean) },
    { key: "median", label: "Median", num: true, render: (r) => fmtDur(r.s.median) },
    { key: "p90", label: "p90", num: true, render: (r) => fmtDur(r.s.p90) },
  ], rows);
}

function drawCharts() {
  if (!last) return;
  const d = last.data;
  const daily = $("#chart-daily");
  if (daily) {
    if (!d.daily_case_starts.dates.length) clear(daily, h("div", { class: "state" }, "No cases in the selection."));
    else plot(daily, [{ type: "bar", x: d.daily_case_starts.dates, y: d.daily_case_starts.counts, marker: { color: cssVar("--chart-1") },
      hovertemplate: "%{x}<br>%{y} case starts<extra></extra>" }], { yaxis: { title: { text: "case starts" } } });
  }
  const dist = $("#chart-dist");
  if (dist) {
    const hist = d.duration_distribution[distMode === "completed_throughput" ? "completed_throughput" : "observed_all_cases"];
    if (!hist.counts.length) clear(dist, h("div", { class: "state" }, distMode === "completed_throughput"
      ? "No completed cases in the selection (check the completion rule on the Datasets page)." : "No cases in the selection."));
    else histogramBars(dist, hist, { color: cssVar(distMode === "completed_throughput" ? "--chart-1" : "--chart-2"), xTitle: "duration (hours)" });
  }
}

async function render() {
  const ds = currentDatasetId();
  if (!ds) { clear($("#overview-exports")); return noDatasetState(root); }
  loading(root, "Computing KPIs…");
  try {
    last = await api(`/api/datasets/${ds}/overview`, { params: new URLSearchParams(location.search) });
  } catch (err) { return showError(root, err); }
  clear($("#overview-exports"), exportLinks(ds, [["summary", "json", "Summary JSON"], ["cases", "csv", "Cases CSV"], ["events", "csv", "Events CSV"]]));
  const d = last.data;
  if (d.case_count === 0) {
    return clear(root, h("div", { class: "card" }, h("div", { class: "state" }, h("div", { class: "title" }, "No cases match the current filters"),
      `The dataset has ${fmtInt(d.total_case_count)} cases. Remove a filter chip above to widen the selection.`)));
  }
  const sc = d.status_counts;
  const popNote = d.case_count === d.total_case_count ? "all cases" : `${fmtInt(d.case_count)} of ${fmtInt(d.total_case_count)} cases (filtered)`;
  const kpis = h("div", { class: "kpis" },
    kpi("Cases", fmtInt(d.case_count), popNote, "Distinct case IDs in the selection"),
    kpi("Events", fmtInt(d.event_count), `${(d.event_count / d.case_count).toFixed(1)} per case`, "Retained events of the selected cases"),
    kpi("Activities", fmtInt(d.activity_count), "distinct names"),
    kpi("Completed", fmtInt(sc.completed), d.completion_mode === "none" ? "no completion rule set" : `${fmtPct(sc.completed / d.case_count)} of cases`),
    kpi("Incomplete / unknown", `${fmtInt(sc.incomplete)} / ${fmtInt(sc.unknown)}`, "not known to be finished"),
    kpi("Median throughput", fmtDur(d.completed_throughput.median), `completed cases (n=${fmtInt(d.completed_throughput.n)})`),
    kpi("Rework rate", fmtPct(d.rework_rate), `${fmtInt(d.rework_case_count)} cases with a repeated activity`),
    kpi("Variants", fmtInt(d.variant_count), "distinct activity sequences"),
  );

  const distToggle = h("div", { class: "actions" },
    h("label", { class: "inline" }, h("input", { type: "radio", name: "dist", value: "completed_throughput", checked: distMode === "completed_throughput",
      onchange: () => { distMode = "completed_throughput"; drawCharts(); } }), "Completed throughput"),
    h("label", { class: "inline" }, h("input", { type: "radio", name: "dist", value: "observed", checked: distMode !== "completed_throughput",
      onchange: () => { distMode = "observed"; drawCharts(); } }), "Observed duration (all)"));

  const topVariants = d.top_variants.length ? table([
    { key: "rank", label: "#", num: true },
    { key: "seq", label: "Activity sequence", render: (v) => variantSequence(v.activities) },
    { key: "case_count", label: "Cases", num: true, render: (v) => `${fmtInt(v.case_count)} (${fmtPct(v.share)})` },
    { key: "median", label: "Median observed", num: true, render: (v) => fmtDur(v.observed_duration.median) },
  ], d.top_variants, { onRowClick: (v) => { location.href = linkWith("/variants", { extra: { select: v.variant_id } }); } })
    : h("div", { class: "state" }, "No variants.");

  const bottlenecks = d.bottleneck_candidates.length ? table([
    { key: "t", label: "Transition", render: (t) => `${t.source} → ${t.target}` },
    { key: "median", label: "Median elapsed", num: true, render: (t) => fmtDur(t.elapsed.median) },
    { key: "p90", label: "p90", num: true, render: (t) => fmtDur(t.elapsed.p90) },
    { key: "cases", label: "Cases", num: true, render: (t) => fmtInt(t.case_count) },
  ], d.bottleneck_candidates, { onRowClick: (t) => { location.href = linkWith("/cases", { filters: { ...readFilters(), transition_from: t.source, transition_to: t.target } }); } })
    : h("div", { class: "state" }, "No transitions observed in at least 5 cases.");

  clear(root,
    kpis,
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Daily case starts"),
        h("span", { class: "small muted" }, `first event date, ${esc(d.daily_case_starts.timezone)}`)), h("div", { id: "chart-daily", class: "chart" })),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Duration distribution"), distToggle),
        h("div", { id: "chart-dist", class: "chart" }))),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Duration statistics"),
      h("span", { class: "small muted" }, "median and p90 use linear interpolation")), durationTable(d)),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Top variants"),
        h("a", { href: linkWith("/variants"), class: "small" }, "All variants →")), topVariants),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Bottleneck candidates"),
        badge("descriptive", "info")),
        h("p", { class: "small muted" }, "Transitions with the longest median elapsed time (≥ 5 cases). Elapsed time between events can include waiting and work; it is a starting point for investigation, not a root cause."),
        bottlenecks)),
  );
  drawCharts();
}

onThemeChange(drawCharts);
bootPage(render);
