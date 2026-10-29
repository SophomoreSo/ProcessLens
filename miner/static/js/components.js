// Rendering of view components from the data the API computed. Clicking a bar, slice,
// row, activity or variant calls onSelect(label, condition) to cross-filter the view.
/* global Plotly, cytoscape, cytoscapeDagre */
import { $, PALETTE, clear, cssVar, equalsCondition, fmtInt, fmtValue, h, plqlLiteral } from "./core.js";

if (typeof cytoscape !== "undefined" && typeof cytoscapeDagre !== "undefined") {
  try { cytoscape.use(cytoscapeDagre); } catch { /* already registered */ }
}

const PLOT_CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };

// Plotly renders a subset of HTML in labels; escape data.
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function label(v, col) {
  if (v === null || v === undefined) return col.kind === "measure" ? "—" : "(missing)";
  return fmtValue(v, col.format === "auto" ? "number" : col.format, col.type);
}

function layout(extra = {}) {
  const text = cssVar("--text");
  const muted = cssVar("--text-muted");
  const grid = cssVar("--border");
  const axis = { gridcolor: grid, linecolor: grid, zerolinecolor: grid, tickfont: { color: muted }, automargin: true };
  return {
    margin: { l: 48, r: 16, t: 8, b: 40 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: getComputedStyle(document.body).fontFamily, size: 12, color: text },
    hoverlabel: { bgcolor: cssVar("--surface"), bordercolor: grid, font: { color: text } },
    showlegend: false, ...extra,
    xaxis: { ...axis, ...(extra.xaxis || {}) }, yaxis: { ...axis, ...(extra.yaxis || {}) },
    ...(extra.yaxis2 ? { yaxis2: { ...axis, ...extra.yaxis2 } } : {}),
  };
}

function tickFormat(col) {
  return col.format === "percent" ? ".0%" : "";
}

function isTimeLike(formula) {
  return /^\s*(YEAR|QUARTER|MONTH|WEEK|DAY|WEEKDAY|HOUR|BUCKET)\s*\(/i.test(formula);
}

/** Bar, line and pie charts over one dimension and one or more measures. */
function chart(el, comp, data, onSelect) {
  const dim = data.columns[0];
  const measures = data.columns.slice(1);
  const rows = data.rows;
  if (!rows.length) { clear(el, h("p", { class: "muted small" }, "No data for the current filters.")); return; }
  const cats = rows.map((r) => label(r[0], dim));
  const raw = rows.map((r) => r[0]);
  const colors = PALETTE();
  const plot = h("div", { class: "plot" });
  clear(el, plot);
  const cfg = comp.config || {};
  let traces;
  let lay;
  if (comp.type === "pie") {
    const m = measures[0];
    traces = [{ type: "pie", hole: 0.55, labels: cats.map(esc), values: rows.map((r) => r[1]), customdata: raw,
      marker: { colors: cats.map((_, i) => colors[i % colors.length]), line: { color: cssVar("--surface"), width: 2 } },
      textinfo: "percent", sort: false, direction: "clockwise",
      hovertemplate: `%{label}<br>${esc(m.label)}: %{text}<extra></extra>`, text: rows.map((r) => label(r[1], m)) }];
    lay = layout({ showlegend: true, legend: { orientation: "v", font: { size: 11 } }, margin: { l: 8, r: 8, t: 8, b: 8 } });
  } else {
    const horizontal = comp.type === "bar" && (cfg.orientation === "h" || (!cfg.orientation || cfg.orientation === "auto")
      && !isTimeLike(dim.formula) && dim.type !== "number" && dim.type !== "datetime");
    const secondAxis = comp.type === "line" && measures.length === 2 && measures[0].format !== measures[1].format;
    traces = measures.map((m, i) => {
      const vals = rows.map((r) => r[i + 1]);
      const t = {
        type: comp.type === "line" ? "scatter" : "bar", name: esc(m.label), customdata: raw,
        text: vals.map((v) => label(v, m)), hovertemplate: `%{${horizontal ? "y" : "x"}}<br>${esc(m.label)}: %{text}<extra></extra>`,
        marker: { color: colors[i % colors.length] },
      };
      if (comp.type === "line") Object.assign(t, { mode: "lines+markers", line: { color: colors[i % colors.length], width: 2.5, shape: "spline", smoothing: 0.4 } });
      else t.textposition = "none";
      if (horizontal) Object.assign(t, { orientation: "h", y: cats.map(esc), x: vals });
      else Object.assign(t, { x: cats.map(esc), y: vals });
      if (secondAxis && i === 1) t.yaxis = "y2";
      return t;
    });
    const valueAxis = { tickformat: tickFormat(measures[0]), rangemode: "tozero" };
    const catAxis = { type: "category", automargin: true };
    lay = layout(horizontal
      ? { xaxis: valueAxis, yaxis: { ...catAxis, autorange: "reversed" }, margin: { l: 8, r: 16, t: 8, b: 32 }, barmode: "group" }
      : { xaxis: catAxis, yaxis: valueAxis, barmode: "group",
        ...(secondAxis ? { yaxis2: { overlaying: "y", side: "right", showgrid: false, tickformat: tickFormat(measures[1]), rangemode: "tozero" } } : {}) });
    if (measures.length > 1) Object.assign(lay, { showlegend: true, legend: { orientation: "h", y: -0.2 } });
    if (horizontal) plot.style.height = `${Math.max(220, Math.min(rows.length * 26 + 50, 640))}px`;
  }
  Plotly.react(plot, traces, lay, PLOT_CONFIG).then(() => {
    plot.on("plotly_click", (ev) => {
      const p = ev.points?.[0];
      if (!p) return;
      const value = p.customdata;
      onSelect?.(`${dim.label} = ${label(value, dim)}`, equalsCondition(dim.formula, value));
    });
  });
}

function table(el, comp, data, onSelect) {
  const cols = data.columns;
  const dims = cols.filter((c) => c.kind === "dimension");
  const wrap = h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.kind === "measure" || c.type === "number" ? "num" : "", title: c.formula }, c.label)))),
    h("tbody", {}, data.rows.map((r) => h("tr", {
      class: dims.length ? "clickable" : "",
      title: dims.length ? "Click to filter the view to this row" : null,
      onclick: dims.length ? () => onSelect?.(dims.map((d, i) => label(r[i], d)).join(" · "),
        dims.map((d, i) => equalsCondition(d.formula, r[i])).join(" AND ")) : null,
    }, r.map((v, i) => h("td", { class: cols[i].kind === "measure" || cols[i].type === "number" ? "num" : "" },
      label(v, cols[i]))))))));
  clear(el, data.rows.length ? wrap : h("p", { class: "muted small" }, "No data for the current filters."),
    data.total_groups > data.rows.length ? h("div", { class: "comp-foot" }, `Showing ${fmtInt(data.rows.length)} of ${fmtInt(data.total_groups)} rows`) : null);
}

function kpiTile(el, comp, data) {
  const fmt = data.format === "auto" ? "number" : data.format;
  let target = null;
  if (data.target !== null && data.target !== undefined && data.value !== null) {
    const ok = data.higher_is_better ? data.value >= data.target : data.value <= data.target;
    target = h("div", { class: `sub target ${ok ? "ok" : "bad"}` },
      `${ok ? "✓" : "✗"} target ${data.higher_is_better ? "≥" : "≤"} ${fmtValue(data.target, fmt)}`);
  }
  clear(el, h("div", { class: "kpi-tile" },
    h("div", { class: "value" }, fmtValue(data.value, fmt)),
    target,
    data.case_count !== data.total_case_count
      ? h("div", { class: "sub" }, `${fmtInt(data.case_count)} of ${fmtInt(data.total_case_count)} cases`) : null));
}

function variantsList(el, comp, data, onSelect) {
  if (!data.items.length) { clear(el, h("p", { class: "muted small" }, "No cases for the current filters.")); return; }
  clear(el, data.items.map((v) => {
    const seen = new Set();
    return h("div", { class: "variant-row", role: "button", tabindex: "0", title: "Click to filter the view to this variant",
      onclick: () => onSelect?.(`Variant #${v.rank}`, `case.variant_id = ${plqlLiteral(v.variant_id)}`),
      onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } },
    h("div", { class: "variant-steps" }, v.activities.map((a, i) => {
      const rep = seen.has(a);
      seen.add(a);
      return [i ? h("span", { class: "arrow", "aria-hidden": "true" }, "→") : null, h("span", { class: `step${rep ? " rep" : ""}` }, a)];
    })),
    h("div", {}, h("div", { class: "small" }, `${fmtInt(v.case_count)} cases · ${fmtValue(v.share, "percent")}`),
      h("div", { class: "bar-meter" }, h("span", { style: `width:${Math.max(1, v.share * 100).toFixed(1)}%` }))));
  }),
  data.total > data.items.length ? h("div", { class: "comp-foot" }, `Top ${data.items.length} of ${fmtInt(data.total)} variants`) : null);
}

const START = "__start__";
const END = "__end__";

function processGraph(el, comp, data, onSelect) {
  if (!data.nodes.length) { clear(el, h("p", { class: "muted small" }, "No cases for the current filters.")); return null; }
  const box = h("div", { class: "graph" });
  // Tall enough that labels stay readable when the top-down layout is fitted into the box.
  box.style.height = `${Math.max(360, Math.min(46 * data.nodes.length + 120, 820))}px`;
  const zoomBy = (f) => cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: box.clientWidth / 2, y: box.clientHeight / 2 } });
  const zoomBar = h("div", { class: "graph-zoom" },
    h("button", { type: "button", class: "btn btn-sm btn-icon", "aria-label": "Zoom in", onclick: () => zoomBy(1.25) }, "+"),
    h("button", { type: "button", class: "btn btn-sm btn-icon", "aria-label": "Zoom out", onclick: () => zoomBy(0.8) }, "−"),
    h("button", { type: "button", class: "btn btn-sm btn-icon", "aria-label": "Fit the graph", onclick: () => cy.fit(undefined, 24) }, "⤢"));
  clear(el, h("div", { class: "graph-wrap" }, box, zoomBar), data.hidden_activities
    ? h("div", { class: "comp-foot" }, `${data.hidden_activities} less frequent activities hidden (raise “max activities” to show them)`) : null);
  const maxEdge = Math.max(1, ...data.edges.map((e) => e.case_count));
  const maxNode = Math.max(1, ...data.nodes.map((n) => n.occurrences));
  const elements = [
    { data: { id: START, label: "Start", kind: "marker" } },
    { data: { id: END, label: "End", kind: "marker" } },
    ...data.nodes.map((n) => ({ data: { id: `n:${n.id}`, act: n.id, label: `${n.label}\n${fmtInt(n.occurrences)}`, kind: "act",
      w: n.occurrences / maxNode, occurrences: n.occurrences, cases: n.case_count } })),
    ...data.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source === START ? START : `n:${e.source}`,
      target: e.target === END ? END : `n:${e.target}`, label: fmtInt(e.case_count), w: e.case_count / maxEdge,
      src: e.source, tgt: e.target, cases: e.case_count, median: e.elapsed?.median } })),
  ];
  const accent = cssVar("--c1");
  const text = cssVar("--text");
  const muted = cssVar("--text-muted");
  const cy = cytoscape({
    // The wheel scrolls the page, not the graph: zoom with the buttons, pan by dragging.
    container: box, elements, userZoomingEnabled: false, minZoom: 0.2, maxZoom: 3,
    style: [
      { selector: "node[kind='act']", style: { shape: "round-rectangle", label: "data(label)", "text-wrap": "wrap",
        "text-valign": "center", "font-size": 11, color: "#fff", width: "label", height: "label", padding: "9px",
        "background-color": accent, "background-opacity": "mapData(w, 0, 1, 0.55, 1)", "text-max-width": "150px" } },
      { selector: "node[kind='marker']", style: { shape: "ellipse", width: 22, height: 22, label: "data(label)",
        "font-size": 10, color: muted, "text-valign": "bottom", "text-margin-y": 4, "background-color": muted } },
      { selector: "edge", style: { "curve-style": "bezier", "target-arrow-shape": "triangle", width: "mapData(w, 0, 1, 1, 7)",
        "line-color": muted, "target-arrow-color": muted, opacity: 0.75, label: "data(label)", "font-size": 9,
        color: text, "text-background-color": cssVar("--surface"), "text-background-opacity": 0.85, "text-background-padding": "2px" } },
      { selector: "edge:selected, node:selected", style: { "overlay-opacity": 0.12, "overlay-color": accent } },
    ],
    layout: { name: "dagre", rankDir: "TB", nodeSep: 30, rankSep: 46, edgeSep: 10, fit: true, padding: 24 },
  });
  const tip = $("#tooltip");
  const showTip = (evt, title, rows) => {
    clear(tip, h("strong", {}, title), h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
    tip.classList.remove("hidden");
    const e = evt.originalEvent;
    tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.min(e.clientY + 14, window.innerHeight - tip.offsetHeight - 8)}px`;
  };
  cy.on("mouseover", "node[kind='act']", (evt) => {
    const d = evt.target.data();
    showTip(evt, d.act, [["Occurrences", fmtInt(d.occurrences)], ["Cases", fmtInt(d.cases)]]);
  });
  cy.on("mouseover", "edge", (evt) => {
    const d = evt.target.data();
    const rows = [["Cases", fmtInt(d.cases)]];
    if (d.median !== null && d.median !== undefined) rows.push(["Median time", fmtValue(d.median / 3600, "hours")]);
    showTip(evt, `${d.src === START ? "Start" : d.src} → ${d.tgt === END ? "End" : d.tgt}`, rows);
  });
  cy.on("mouseout", () => tip.classList.add("hidden"));
  cy.on("tap", "node[kind='act']", (evt) => {
    const a = evt.target.data("act");
    onSelect?.(`Has ${a}`, `CONTAINS(${plqlLiteral(a)})`);
  });
  return () => { tip.classList.add("hidden"); cy.destroy(); };
}

/** Render ``data`` into ``el``. Returns a cleanup function (or null). */
export function renderComponent(el, comp, data, onSelect) {
  switch (comp.type) {
    case "kpi": kpiTile(el, comp, data); return null;
    case "bar": case "line": case "pie": chart(el, comp, data, onSelect); return () => Plotly.purge(el.querySelector(".plot") || el);
    case "table": table(el, comp, data, onSelect); return null;
    case "variants": variantsList(el, comp, data, onSelect); return null;
    case "process": return processGraph(el, comp, data, onSelect);
    case "text": clear(el, h("div", { class: "text-comp" }, comp.config?.text || "")); return null;
    default: clear(el, h("p", { class: "muted" }, `Unknown component type ${comp.type}`)); return null;
  }
}

export const COMPONENT_TYPES = [
  ["kpi", "KPI tile", "One value, with its target"],
  ["bar", "Bar chart", "A measure per dimension value"],
  ["line", "Line chart", "Trends over time"],
  ["pie", "Donut chart", "Shares of a whole"],
  ["table", "Table", "Several dimensions and measures"],
  ["process", "Process graph", "Directly-follows graph"],
  ["variants", "Variants", "Most frequent paths"],
  ["text", "Text", "Notes and headings"],
];

export { chart as renderChart, table as renderTable };
