import { onThemeChange } from "./charts.js";
import {
  $, api, bootPage, clear, currentDatasetId, exportLinks, fmtInt, h, linkWith, loading, noDatasetState, readFilters,
  showError,
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
    $("#visibility-note").textContent = `Showing ${shownNodes} of ${totalNodes} activities and ${shownEdges} of ${totalEdges} edges (including start/end edges). Hidden elements remain part of every calculation.`;
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
  loading(container, "Building the directly-follows graph…");
  let res;
  try {
    res = await api(`/api/datasets/${ds}/graph`, { params: new URLSearchParams(location.search) });
  } catch (err) { return showError(container, err); }
  clear($("#process-exports"), exportLinks(ds, [["transitions", "csv", "Transitions CSV"], ["events", "xes", "Events XES"]]));
  const d = res.data;
  clear($("#graph-stats"),
    h("p", {}, `${fmtInt(d.case_count)} cases (of ${fmtInt(res.population.total_case_count)}) · ${fmtInt(d.event_count)} events · ${d.nodes.length} activities · ${d.edges.length} edges incl. start/end`),
    h("ul", { class: "small muted" }, d.notes.map((n) => h("li", {}, n))));
  if (!d.nodes.length) {
    return clear(container, h("div", { class: "state" }, h("div", { class: "title" }, "No cases match the current filters"), "Remove a filter chip to widen the selection."));
  }
  clear(container);
  graph.setData(d);
  graph.setMode($("#mode").value, $("#metric").value);
  thresholds();
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
onThemeChange(() => graph.restyle());
bootPage(render);
