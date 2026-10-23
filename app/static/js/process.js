import { onThemeChange } from "./charts.js";
import {
  $, $$, api, bootPage, clear, currentDatasetId, exportLinks, fmtDur, fmtInt, h, linkWith, loading,
  motionEnabled, noDatasetState, objectTypes, readFilters, showError, t, table,
} from "./core.js";
import { ProcessGraph } from "./graph.js";
import { renderModel, typeColor } from "./models.js";

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

// ---------------------------------------------------------------- object-centric DFG (all object types)
let view = new URLSearchParams(location.search).get("view") === "oc" ? "oc" : "lead";
let ocTypes = null;

function selectView(next) {
  view = next;
  $$("#proc-view .tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  $("#ocdfg-card").classList.toggle("hidden", view !== "oc");
  $("#lead-dfg-card").classList.toggle("hidden", view === "oc");
  $("#lead-dfg-extra").classList.toggle("hidden", view === "oc");
  const p = new URLSearchParams(location.search);
  if (view === "oc") p.set("view", "oc"); else p.delete("view");
  history.replaceState(null, "", `${location.pathname}?${p}`);
  if (view === "oc") drawOcdfg();
}

async function drawOcdfg() {
  const ds = currentDatasetId();
  if (!ds) return noDatasetState($("#ocdfg-view"));
  const names = objectTypes().map((x) => x.name);
  if (ocTypes === null) ocTypes = [...names];
  clear($("#ocdfg-types"), names.map((ty) => h("label", { class: "inline" },
    h("input", { type: "checkbox", value: ty, checked: ocTypes.includes(ty), onchange: () => {
      ocTypes = $$("#ocdfg-types input:checked").map((i) => i.value);
      drawOcdfg();
    } }), h("i", { class: "swatch", style: { background: typeColor(names, ty), width: "10px", height: "10px", borderRadius: "50%", display: "inline-block" } }), ty)));
  if (!ocTypes.length) return clear($("#ocdfg-view"), h("p", { class: "muted" }, t("ocel.empty_selection")));
  try {
    const g = await api(`/api/datasets/${ds}/ocel/ocdfg`, { params: { object_type: ocTypes.length === names.length ? [] : ocTypes } });
    g.object_types = names.filter((n) => g.object_types.includes(n));  // stable colours across selections
    renderModel($("#ocdfg-view"), g, { height: 580 });
    clear($("#ocdfg-table"), h("details", {}, h("summary", {}, t("ocel.edge_table")), table([
      { key: "object_type", label: t("ocel.object_type") },
      { key: "source", label: t("model.from") },
      { key: "target", label: t("model.to") },
      { key: "object_transitions", label: t("ocel.object_transitions"), num: true, render: (e) => fmtInt(e.object_transitions) },
      { key: "objects", label: t("ocel.objects"), num: true, render: (e) => fmtInt(e.objects) },
      { key: "pairs", label: t("ocel.unique_event_pairs"), num: true, render: (e) => fmtInt(e.unique_event_pairs) },
      { key: "median", label: t("ocel.median_elapsed"), num: true, render: (e) => fmtDur(e.median_elapsed_s) },
    ], g.edges)));
  } catch (err) { showError($("#ocdfg-view"), err); }
}

$$("#proc-view .tab").forEach((b) => b.addEventListener("click", () => selectView(b.dataset.view)));

async function render() {
  ocTypes = null;
  selectView(view);
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
