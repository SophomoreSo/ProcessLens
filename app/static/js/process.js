import { onThemeChange } from "./charts.js";
import {
  $, api, bootPage, clear, currentDatasetId, exportLinks, fmtInt, h, linkWith, loading, motionEnabled, noDatasetState,
  readFilters, showError, t,
} from "./core.js";
import { ProcessGraph } from "./graph.js";

const container = $("#graph");
const graph = new ProcessGraph(container, {
  onNodeClick: (activity) => { location.href = linkWith("/cases", { filters: { ...readFilters(), activity: [...new Set([...(readFilters().activity || []), activity])] } }); },
  onEdgeClick: (a, b) => {
    if (a === "__start__" && b === "__end__") return;
    location.href = linkWith("/cases", { filters: { ...readFilters(), transition_from: a, transition_to: b } });
  },
  onVisibility: ({ shownNodes, totalNodes, shownEdges, totalEdges }) => {
    $("#visibility-note").textContent = t("proc.visibility", { shownNodes, totalNodes, shownEdges, totalEdges });
  },
});

function thresholds() {
  const n = Number($("#node-thr").value);
  const e = Number($("#edge-thr").value);
  $("#node-thr-out").textContent = `${n}%`;
  $("#edge-thr-out").textContent = `${e}%`;
  graph.setThresholds(n / 100, e / 100);
}

async function render() {
  const ds = currentDatasetId();
  graph.setData(null);  // destroy any previous Cytoscape instance before reusing the container
  if (!ds) { clear($("#process-exports")); return noDatasetState(container); }
  loading(container, t("proc.building"));
  let res;
  try {
    res = await api(`/api/datasets/${ds}/graph`, { params: new URLSearchParams(location.search) });
  } catch (err) { return showError(container, err); }
  clear($("#process-exports"), exportLinks(ds, [["transitions", "csv", t("export.transitions_csv")], ["events", "xes", t("export.events_xes")]]));
  const d = res.data;
  clear($("#graph-stats"),
    h("p", {}, t("proc.stats", { cases: fmtInt(d.case_count), total: fmtInt(res.population.total_case_count), events: fmtInt(d.event_count), nodes: d.nodes.length, edges: d.edges.length })),
    h("ul", { class: "small muted" }, d.notes.map((n) => h("li", {}, n))));
  if (!d.nodes.length) {
    return clear(container, h("div", { class: "state" }, h("div", { class: "title" }, t("empty.no_match.title")), t("empty.no_match.hint")));
  }
  clear(container);
  graph.setData(d);
  graph.setMode($("#mode").value, $("#metric").value);
  thresholds();
  graph.setFlow($("#flow-toggle").checked);
}

function syncFlowToggle() {
  const on = motionEnabled();
  $("#flow-toggle").disabled = !on;
  $("#flow-toggle-label").title = on ? t("proc.animate_flow_title") : t("proc.animate_flow_disabled");
  if (!on) { $("#flow-toggle").checked = false; graph.setFlow(false); }
}

$("#mode").addEventListener("change", () => {
  $("#metric").disabled = $("#mode").value !== "elapsed";
  graph.setMode($("#mode").value, $("#metric").value);
});
$("#metric").addEventListener("change", () => graph.setMode($("#mode").value, $("#metric").value));
$("#node-thr").addEventListener("input", thresholds);
$("#edge-thr").addEventListener("input", thresholds);
$("#zoom-in").addEventListener("click", () => graph.zoom(1.25));
$("#zoom-out").addEventListener("click", () => graph.zoom(0.8));
$("#fit").addEventListener("click", () => graph.fit());
$("#reset").addEventListener("click", () => graph.reset());
$("#flow-toggle").addEventListener("change", (e) => graph.setFlow(e.target.checked));
window.addEventListener("motionchange", syncFlowToggle);
syncFlowToggle();
$("#flow-toggle").checked = motionEnabled();
onThemeChange(() => graph.restyle());
bootPage(render);
