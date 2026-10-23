// Guided analysis: pick a question, set its parameters, get a fully documented answer.
import { cssVar, esc, render as plot } from "./charts.js";
import {
  $, $$, api, badge, bootPage, clear, currentDataset, currentDatasetId, currentObjectType, filterParams, fmtDur, fmtInt,
  fmtNum, fmtPct, h, noDatasetState, perspectiveLink, readFilters, reveal, showError, t, table,
} from "./core.js";

let questions = [];
let selected = null;

const sp = () => new URLSearchParams(location.search);

function selectQuestion(id) {
  selected = questions.find((q) => q.id === id) || null;
  $$(".question").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.id === id)));
  if (!selected) return;
  $("#q-title").textContent = t(selected.title_key);
  $("#q-desc").textContent = t(selected.description_key);
  drawSource();
  drawParams();
  const p = new URLSearchParams(location.search);
  p.set("q", id);
  history.replaceState(null, "", `${location.pathname}?${p}`);
}

function drawSource() {
  const box = $("#q-source");
  $("#ask").disabled = false;
  const ds = currentDataset();
  if (!ds) { $("#ask").disabled = true; noDatasetState(box); return; }
  const filters = readFilters();
  clear(box, h("p", { class: "small" }, t("guide.on_dataset_type", { name: ds.name, version: ds.version, type: currentObjectType() }), " ",
    Object.keys(filters).length ? badge(t("guide.filters_applied", { n: Object.keys(filters).length }), "info") : badge(t("guide.no_filters"))));
}

function drawParams() {
  const form = $("#q-params");
  const urlP = sp();
  const fields = selected.params.map((p) => {
    const v = urlP.get(p.name) ?? p.default;
    let input;
    if (p.type === "choice") {
      input = h("select", { name: p.name }, p.choices.map((c) => h("option", { value: c, selected: String(v) === c }, t(`guide.choice.${c}`))));
    } else {
      input = h("input", { type: "number", name: p.name, value: v ?? "", min: p.minimum ?? "", max: p.maximum ?? "",
        step: p.type === "int" ? "1" : "any", placeholder: v === null ? t("guide.optional") : "" });
    }
    return h("label", {}, h("span", {}, t(p.label_key)), input);
  });
  clear(form, fields.length ? fields : h("p", { class: "small muted" }, t("guide.no_params")));
}

function fmtCell(format, v) {
  if (v === null || v === undefined) return "—";
  if (format === "duration") return fmtDur(v);
  if (format === "int") return fmtInt(v);
  if (format === "pct") return fmtPct(v, 1);
  if (format === "status") return t(`status.${v}`);
  if (format === "via") return t(`guide.via.${v}`);
  return String(v);
}

function drawChart(el, chart) {
  const color = cssVar("--chart-1");
  if (chart.kind === "histogram") {
    const e = chart.edges;
    const x = chart.counts.map((_, i) => (e[i] + e[i + 1]) / 2);
    const w = chart.counts.map((_, i) => e[i + 1] - e[i]);
    plot(el, [{ type: "bar", x, y: chart.counts, width: w, marker: { color },
      hovertemplate: `%{x:.1f} h<br>%{y} ${esc(t("guide.col.cases"))}<extra></extra>` }],
    { xaxis: { title: { text: t("chart.hours") } }, yaxis: { title: { text: t("guide.col.cases") } }, bargap: 0.04,
      shapes: [{ type: "line", x0: chart.threshold, x1: chart.threshold, yref: "paper", y0: 0, y1: 1, line: { color: cssVar("--danger"), width: 2, dash: "dash" } }],
      annotations: [{ x: chart.threshold, yref: "paper", y: 1, text: esc(t("guide.threshold", { hours: fmtNum(chart.threshold, 1) })), showarrow: false, xanchor: "left", font: { color: cssVar("--danger") } }] });
    return;
  }
  const isSeconds = chart.unit === "seconds";
  const values = isSeconds ? chart.values.map((v) => v / 3600) : chart.values;
  const text = chart.values.map((v) => (isSeconds ? fmtDur(v) : fmtInt(v)));
  plot(el, [{ type: "bar", orientation: "h", y: chart.labels.map(esc).reverse(), x: [...values].reverse(), text: [...text].reverse(),
    textposition: "auto", marker: { color }, customdata: (chart.hover || chart.labels).map(esc).reverse(),
    hovertemplate: "%{customdata}<br>%{text}<extra></extra>" }],
  { margin: { l: 220, r: 16, t: 10, b: 48 }, xaxis: { title: { text: `${t(chart.value_label_key)}${isSeconds ? ` (${t("chart.hours")})` : ""}` } },
    yaxis: { automargin: true } });
}

function provenanceBlock(a) {
  const pv = a.provenance;
  const rows = pv.source === "ocel"
    ? [[t("guide.prov.ocel_log"), `${pv.ocel_log_name} (#${pv.ocel_log_id}), ${t("guide.prov.version", { v: pv.ocel_version })}`],
      [t("guide.prov.selection"), `${t("ocel.object_type")}: ${pv.filters.object_type}`]]
    : [[t("guide.prov.dataset"), `${pv.dataset_name} (#${pv.dataset_id}), ${t("guide.prov.version", { v: pv.dataset_version })} · ${t("guide.prov.settings", { r: pv.settings_revision })}`],
      [t("guide.prov.object_type"), pv.object_type],
      [t("guide.prov.filters"), Object.keys(pv.filters).length ? h("code", { class: "small" }, JSON.stringify(pv.filters)) : t("guide.no_filters")],
      [t("guide.prov.population"), t("guide.prov.population_value", { n: fmtInt(pv.population.cases), total: fmtInt(pv.population.total_cases) })]];
  rows.push([t("guide.prov.sample"), `${fmtInt(pv.sample.n)} ${t(pv.sample.label_key)}`]);
  rows.push([t("guide.prov.params"), h("code", { class: "small" }, JSON.stringify(a.params))]);
  return h("dl", { class: "prov" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function headlineText(hl) {
  const p = { ...hl.params };
  for (const k of Object.keys(p)) {
    if (k === "seconds" || k.endsWith("_s")) p[k] = fmtDur(p[k]);
    else if (k === "share") p[k] = fmtPct(p[k], 1);
    else if (k === "statistic") p[k] = t(`guide.stat.${p[k]}`);
    else if (k === "hours") p[k] = fmtNum(p[k], 1);
    else if (typeof p[k] === "number") p[k] = fmtInt(p[k]);
  }
  return t(hl.key, p);
}

function drawAnswer(a) {
  const card = $("#answer-card");
  card.classList.remove("hidden");
  const chartEl = h("div", { class: "answer-chart" });
  const caseLink = (v) => h("a", { class: "mono", href: perspectiveLink("/cases", a.provenance.object_type, { case: v }) }, v);
  clear(card,
    h("div", { class: "card-head" }, h("h2", {}, t(a.title_key)), a.status === "answered" ? badge(t("guide.answered"), "ok") : badge(t("guide.not_answerable"), "warn")),
    a.status === "answered" ? h("p", { class: "answer-headline" }, headlineText(a.headline)) : h("p", { class: "notice warn" }, t(a.reason.key, a.reason.params)),
    h("div", { class: "answer-meta" },
      h("div", {}, h("b", {}, t("guide.metric")), " ", t(a.metric.name_key), h("p", { class: "small muted", style: { margin: "4px 0 0" } }, t(a.metric.definition_key))),
      h("div", {}, provenanceBlock(a))),
    a.chart ? chartEl : null,
    a.table ? table(a.table.columns.map((c) => ({ key: c.key, label: t(c.label_key), num: ["int", "duration", "pct"].includes(c.format),
      render: (r) => (c.format === "case_link" ? caseLink(r[c.key]) : fmtCell(c.format, r[c.key])) })), a.table.rows) : null,
    a.caveat_keys.length ? h("ul", { class: "small muted" }, a.caveat_keys.map((k) => h("li", {}, t(k)))) : null,
    a.links.length ? h("div", { class: "actions" }, a.links.map((l) => h("a", { class: "btn btn-sm", href: l.href }, t(l.label_key)))) : null);
  if (a.chart) drawChart(chartEl, a.chart);
  reveal(card);
}

async function ask() {
  if (!selected) return;
  const params = {};
  for (const el of $("#q-params").elements) if (el.name && el.value !== "") params[el.name] = el.value;
  const qs = new URLSearchParams({ question: selected.id, dataset_id: currentDatasetId(), ...params });
  for (const [k, v] of filterParams().entries()) qs.append(k, v);
  $("#ask").disabled = true;
  try {
    drawAnswer(await api(`/api/guide/answer?${qs}`));
    const u = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(params)) u.set(k, v);
    history.replaceState(null, "", `${location.pathname}?${u}`);
  } catch (err) { $("#answer-card").classList.remove("hidden"); showError($("#answer-card"), err); } finally { $("#ask").disabled = false; }
}

async function render() {
  try {
    if (!questions.length) questions = (await api("/api/guide/questions")).questions;
  } catch (err) { showError($("#question-list"), err); return; }
  clear($("#question-list"), questions.map((q) => h("button", { type: "button", class: "question", role: "radio", "aria-checked": "false",
    "data-id": q.id, onclick: () => selectQuestion(q.id) },
  h("div", { class: "q-title" }, t(q.title_key)),
  h("div", { class: "small muted" }, t(q.description_key)))));
  const want = sp().get("q");
  if (want) selectQuestion(want);
  else if (selected) selectQuestion(selected.id);
}

$("#ask").addEventListener("click", ask);
bootPage(render);
