import { cssVar, esc, histogramBars, onThemeChange, render as plot } from "./charts.js";
import {
  $, api, badge, bootPage, clear, countUp, currentDatasetId, exportLinks, fmtDur, fmtInt, fmtNum, fmtPct, h, linkWith,
  loading, noDatasetState, readFilters, reveal, showError, t, table, variantSequence,
} from "./core.js";

const root = $("#overview-root");
let last = null;
let distMode = "completed_throughput";

/** KPI tile. ``value`` may be a number (animated with ``format``) or a ready string. */
function kpi(label, value, sub, { title, format } = {}) {
  const v = h("div", { class: "value" });
  if (typeof value === "number" && format) countUp(v, value, format);
  else v.textContent = value;
  return h("div", { class: "kpi", title }, h("div", { class: "label" }, label), v, sub ? h("div", { class: "sub" }, sub) : null);
}

function durationTable(d) {
  const rows = [
    { pop: t("ov.dur.observed"), s: d.observed_duration, note: t("ov.dur.observed_note") },
    { pop: t("ov.dur.throughput"), s: d.completed_throughput,
      note: d.completion_mode === "none" ? t("ov.dur.throughput_note_none") : t("ov.dur.throughput_note") },
    { pop: t("ov.dur.age"), s: d.open_case_age,
      note: t("ov.dur.age_note", { reference: d.reference_time_source === "configured" ? t("ov.dur.ref_configured") : t("ov.dur.ref_latest") }) },
  ];
  return table([
    { key: "pop", label: t("ov.dur.population"), render: (r) => h("div", {}, r.pop, h("div", { class: "small muted" }, r.note)) },
    { key: "n", label: t("ov.dur.n"), num: true, render: (r) => fmtInt(r.s.n) },
    { key: "mean", label: t("stat.mean"), num: true, render: (r) => fmtDur(r.s.mean) },
    { key: "median", label: t("stat.median"), num: true, render: (r) => fmtDur(r.s.median) },
    { key: "p90", label: t("stat.p90"), num: true, render: (r) => fmtDur(r.s.p90) },
  ], rows);
}

function drawCharts() {
  if (!last) return;
  const d = last.data;
  const daily = $("#chart-daily");
  if (daily) {
    if (!d.daily_case_starts.dates.length) clear(daily, h("div", { class: "state" }, t("ov.no_cases_selected")));
    else plot(daily, [{ type: "bar", x: d.daily_case_starts.dates, y: d.daily_case_starts.counts, marker: { color: cssVar("--chart-1") },
      hovertemplate: "%{x}<br>%{y} " + esc(t("ov.case_starts")) + "<extra></extra>" }], { yaxis: { title: { text: t("ov.case_starts") } } });
  }
  const dist = $("#chart-dist");
  if (dist) {
    const hist = d.duration_distribution[distMode === "completed_throughput" ? "completed_throughput" : "observed_all_cases"];
    if (!hist.counts.length) clear(dist, h("div", { class: "state" }, distMode === "completed_throughput" ? t("ov.no_completed") : t("ov.no_cases_selected")));
    else histogramBars(dist, hist, { color: cssVar(distMode === "completed_throughput" ? "--chart-1" : "--chart-2"), xTitle: t("ov.duration_hours") });
  }
}

async function render() {
  const ds = currentDatasetId();
  if (!ds) { clear($("#overview-exports")); return noDatasetState(root); }
  loading(root, t("ov.computing"));
  try {
    last = await api(`/api/datasets/${ds}/overview`, { params: new URLSearchParams(location.search) });
  } catch (err) { return showError(root, err); }
  clear($("#overview-exports"), exportLinks(ds, [["summary", "json", t("export.summary_json")], ["cases", "csv", t("export.cases_csv")], ["events", "csv", t("export.events_csv")]]));
  const d = last.data;
  if (d.case_count === 0) {
    return clear(root, h("div", { class: "card" }, h("div", { class: "state" }, h("div", { class: "title" }, t("empty.no_match.title")),
      t("empty.no_match.hint_total", { total: fmtInt(d.total_case_count) }))));
  }
  const sc = d.status_counts;
  const popNote = d.case_count === d.total_case_count ? t("ov.all_cases") : t("ov.filtered_cases", { n: fmtInt(d.case_count), total: fmtInt(d.total_case_count) });
  const kpis = h("div", { class: "kpis" },
    kpi(t("ov.kpi.cases"), d.case_count, popNote, { title: t("ov.kpi.cases_title"), format: fmtInt }),
    kpi(t("ov.kpi.events"), d.event_count, t("ov.kpi.per_case", { n: fmtNum(d.event_count / d.case_count) }), { title: t("ov.kpi.events_title"), format: fmtInt }),
    kpi(t("ov.kpi.activities"), d.activity_count, t("ov.kpi.distinct_names"), { format: fmtInt }),
    kpi(t("ov.kpi.completed"), sc.completed, d.completion_mode === "none" ? t("ov.kpi.no_rule") : t("ov.kpi.of_cases", { pct: fmtPct(sc.completed / d.case_count) }), { format: fmtInt }),
    kpi(t("ov.kpi.incomplete_unknown"), `${fmtInt(sc.incomplete)} / ${fmtInt(sc.unknown)}`, t("ov.kpi.not_known_finished")),
    kpi(t("ov.kpi.median_throughput"), fmtDur(d.completed_throughput.median), t("ov.kpi.completed_n", { n: fmtInt(d.completed_throughput.n) })),
    kpi(t("ov.kpi.rework_rate"), d.rework_rate, t("ov.kpi.rework_cases", { n: fmtInt(d.rework_case_count) }), { format: (v) => fmtPct(v) }),
    kpi(t("ov.kpi.variants"), d.variant_count, t("ov.kpi.distinct_sequences"), { format: fmtInt }),
  );

  const distToggle = h("div", { class: "actions" },
    h("label", { class: "inline" }, h("input", { type: "radio", name: "dist", value: "completed_throughput", checked: distMode === "completed_throughput",
      onchange: () => { distMode = "completed_throughput"; drawCharts(); } }), t("ov.dist.throughput")),
    h("label", { class: "inline" }, h("input", { type: "radio", name: "dist", value: "observed", checked: distMode !== "completed_throughput",
      onchange: () => { distMode = "observed"; drawCharts(); } }), t("ov.dist.observed")));

  const topVariants = d.top_variants.length ? table([
    { key: "rank", label: "#", num: true },
    { key: "seq", label: t("var.sequence"), render: (v) => variantSequence(v.activities) },
    { key: "case_count", label: t("common.cases"), num: true, render: (v) => `${fmtInt(v.case_count)} (${fmtPct(v.share)})` },
    { key: "median", label: t("var.median_observed"), num: true, render: (v) => fmtDur(v.observed_duration.median) },
  ], d.top_variants, { onRowClick: (v) => { location.href = linkWith("/variants", { extra: { select: v.variant_id } }); } })
    : h("div", { class: "state" }, t("var.none"));

  const bottlenecks = d.bottleneck_candidates.length ? table([
    { key: "t", label: t("perf.transition"), render: (tr) => `${tr.source} → ${tr.target}` },
    { key: "median", label: t("perf.median_elapsed"), num: true, render: (tr) => fmtDur(tr.elapsed.median) },
    { key: "p90", label: t("stat.p90"), num: true, render: (tr) => fmtDur(tr.elapsed.p90) },
    { key: "cases", label: t("common.cases"), num: true, render: (tr) => fmtInt(tr.case_count) },
  ], d.bottleneck_candidates, { onRowClick: (tr) => { location.href = linkWith("/cases", { filters: { ...readFilters(), transition_from: tr.source, transition_to: tr.target } }); } })
    : h("div", { class: "state" }, t("ov.no_bottlenecks"));

  clear(root,
    kpis,
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, t("ov.daily_starts")),
        h("span", { class: "small muted" }, t("ov.daily_starts_note", { tz: d.daily_case_starts.timezone }))), h("div", { id: "chart-daily", class: "chart" })),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, t("ov.duration_distribution")), distToggle),
        h("div", { id: "chart-dist", class: "chart" }))),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, t("ov.duration_statistics")),
      h("span", { class: "small muted" }, t("ov.interpolation_note"))), durationTable(d)),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, t("ov.top_variants")),
        h("a", { href: linkWith("/variants"), class: "small" }, t("ov.all_variants"))), topVariants),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, t("ov.bottlenecks")),
        badge(t("common.descriptive"), "info")),
        h("p", { class: "small muted" }, t("ov.bottlenecks_note")),
        bottlenecks)),
  );
  reveal(root);
  reveal($(".kpis", root), { stagger: 35 });
  drawCharts();
}

onThemeChange(drawCharts);
bootPage(render);
