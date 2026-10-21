// Thin Plotly.js wrappers. Values come pre-computed from the API.
/* global Plotly */
import { LANG, motionEnabled, t } from "./core.js";

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Plotly renders a subset of HTML in labels; escape user-provided strings.
export function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function baseLayout(extra = {}) {
  const text = cssVar("--text");
  const muted = cssVar("--text-muted");
  const grid = cssVar("--border");
  return {
    margin: { l: 56, r: 16, t: 10, b: 48 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: getComputedStyle(document.body).fontFamily, size: 12, color: text },
    xaxis: { gridcolor: grid, linecolor: grid, zerolinecolor: grid, tickfont: { color: muted }, automargin: true },
    yaxis: { gridcolor: grid, linecolor: grid, zerolinecolor: grid, tickfont: { color: muted }, automargin: true },
    hoverlabel: { bgcolor: cssVar("--surface"), bordercolor: grid, font: { color: text } },
    showlegend: false,
    ...extra,
  };
}

// Plotly locale bundles (ko, pl) are loaded by the page templates and registered by name.
const CONFIG = { displaylogo: false, responsive: true, locale: LANG,
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };
const TRANSITION = { duration: 600, easing: "cubic-in-out" };

/** Copy of bar traces with their value axis set to zero, used as the starting frame of an entrance animation. */
function zeroed(traces) {
  return traces.map((tr) => {
    if (tr.type !== "bar") return tr;
    const key = tr.orientation === "h" ? "x" : "y";
    return { ...tr, [key]: (tr[key] || []).map(() => 0) };
  });
}

export function render(el, traces, layout = {}) {
  const merged = baseLayout(layout);
  if (layout.xaxis) merged.xaxis = { ...baseLayout().xaxis, ...layout.xaxis };
  if (layout.yaxis) merged.yaxis = { ...baseLayout().yaxis, ...layout.yaxis };
  if (!motionEnabled()) {
    Plotly.react(el, traces, merged, CONFIG);
    return;
  }
  const animated = { ...merged, transition: TRANSITION };
  const firstDraw = !el.classList.contains("js-plotly-plot");
  if (firstDraw && traces.some((tr) => tr.type === "bar")) {
    // Fix the value axis range so bars grow into place instead of the axis rescaling.
    Plotly.newPlot(el, zeroed(traces), merged, CONFIG).then(() => Plotly.react(el, traces, animated, CONFIG));
  } else {
    Plotly.react(el, traces, animated, CONFIG);
  }
}

export function histogramBars(el, hist, { color = cssVar("--chart-1"), name = t("chart.cases"), xTitle = t("chart.hours") } = {}) {
  const edges = hist.edges || [];
  const centers = hist.counts.map((_, i) => (edges[i] + edges[i + 1]) / 2);
  const widths = hist.counts.map((_, i) => edges[i + 1] - edges[i]);
  const labels = hist.counts.map((_, i) => t("chart.bin_hours", { from: edges[i].toFixed(1), to: edges[i + 1].toFixed(1) }));
  render(el, [{
    type: "bar", x: centers, y: hist.counts, width: widths, marker: { color }, name,
    customdata: labels, hovertemplate: "%{customdata}<br>%{y} " + esc(name) + "<extra></extra>",
  }], { xaxis: { title: { text: xTitle } }, yaxis: { title: { text: t("chart.cases") } }, bargap: 0.04 });
}

export function onThemeChange(fn) {
  window.addEventListener("themechange", fn);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fn);
}
