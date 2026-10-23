// Rendering of discovered models in their native representation.
// Graph-like models use Cytoscape.js (+ dagre); declarative models are tables.
// All labels come from data and are rendered as text (canvas or textContent).
/* global cytoscape, cytoscapeDagre */
import { cssVar } from "./charts.js";
import { clear, fmtDur, fmtInt, fmtNum, h, motionEnabled, t, table } from "./core.js";

if (typeof cytoscape !== "undefined" && typeof cytoscapeDagre !== "undefined") {
  try { cytoscape.use(cytoscapeDagre); } catch { /* already registered */ }
}

const TREE_SYMBOL = { sequence: "→", xor: "×", parallel: "∧", loop: "↺", or: "∨" };
const GATEWAY_SYMBOL = { exclusive_gateway: "×", parallel_gateway: "+", inclusive_gateway: "○" };

function baseStyle() {
  const text = cssVar("--text");
  const surface = cssVar("--surface");
  const accent = cssVar("--accent");
  const border = cssVar("--border-strong");
  const muted = cssVar("--text-muted");
  return [
    { selector: "node", style: { label: "data(label)", "font-size": 11, color: text, "text-wrap": "wrap",
      "text-max-width": 140, "text-valign": "center", "text-halign": "center", "background-color": surface,
      "border-width": 2, "border-color": accent, width: 150, height: 40, shape: "round-rectangle" } },
    { selector: "edge", style: { "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": border,
      "target-arrow-color": border, width: 1.6, label: "data(label)", "font-size": 9, color: muted,
      "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": 2 } },
    { selector: 'node[kind = "place"]', style: { shape: "ellipse", width: 26, height: 26, label: "data(label)",
      "border-color": muted, "font-size": 14 } },
    { selector: 'node[kind = "place"][?initial]', style: { "border-color": accent, "border-width": 3 } },
    { selector: 'node[kind = "place"][?final]', style: { "border-style": "double", "border-width": 5, "border-color": accent } },
    { selector: 'node[kind = "silent"]', style: { width: 12, height: 34, "background-color": text, "border-width": 0, label: "" } },
    { selector: 'node[kind = "operator"]', style: { shape: "ellipse", width: 36, height: 36, "font-size": 18,
      "background-color": cssVar("--accent-soft"), "border-color": accent } },
    { selector: 'node[kind = "tau"]', style: { shape: "ellipse", width: 26, height: 26, "background-color": surface,
      "border-color": muted, "border-style": "dashed" } },
    { selector: 'node[kind = "gateway"]', style: { shape: "diamond", width: 40, height: 40, "font-size": 16,
      "border-color": cssVar("--accent-2") } },
    { selector: 'node[kind = "start_event"]', style: { shape: "ellipse", width: 28, height: 28, label: "",
      "border-color": cssVar("--ok"), "border-width": 2 } },
    { selector: 'node[kind = "end_event"]', style: { shape: "ellipse", width: 28, height: 28, label: "",
      "border-color": cssVar("--danger"), "border-width": 5 } },
    { selector: 'node[kind = "state"]', style: { shape: "ellipse", width: 120, height: 34, "font-size": 9 } },
    { selector: 'node[kind = "marker"]', style: { shape: "ellipse", width: 60, height: 26, "background-color": muted,
      color: surface, "border-width": 0 } },
  ];
}

function layout(rankDir = "LR", animate = false) {
  const motion = animate && motionEnabled() ? { animate: true, animationDuration: 500 } : { animate: false };
  if (typeof cytoscapeDagre !== "undefined") return { name: "dagre", rankDir, nodeSep: 25, rankSep: 55, padding: 16, ...motion };
  return { name: "breadthfirst", directed: true, padding: 16, ...motion };
}

// --------------------------------------------------------------------------- element builders
function petriElements(rep) {
  const els = [];
  for (const p of rep.places) {
    const tokens = p.initial ? "●".repeat(Math.min(p.initial, 3)) : "";
    els.push({ data: { id: p.id, kind: "place", label: tokens, initial: p.initial > 0, final: p.final > 0,
      tip: [[t("model.place"), p.name], [t("model.initial_tokens"), p.initial], [t("model.final_tokens"), p.final]] } });
  }
  for (const tr of rep.transitions) {
    els.push({ data: { id: tr.id, kind: tr.silent ? "silent" : "transition", label: tr.label || "τ",
      tip: [[tr.silent ? t("model.silent_transition") : t("model.transition"), tr.label || "τ"]] } });
  }
  rep.arcs.forEach((a, i) => els.push({ data: { id: `a${i}`, source: a.source, target: a.target, label: a.weight > 1 ? String(a.weight) : "" } }));
  return els;
}

function treeElements(rep) {
  const els = [];
  let n = 0;
  const walk = (node, parent) => {
    const id = `n${n++}`;
    if (node.children) {
      els.push({ data: { id, kind: "operator", label: TREE_SYMBOL[node.kind] || node.operator, tip: [[t("model.operator"), t(`model.op.${node.kind}`)]] } });
      node.children.forEach((c) => walk(c, id));
    } else if (node.kind === "silent") {
      els.push({ data: { id, kind: "tau", label: "τ", tip: [[t("model.silent_step"), "τ"]] } });
    } else {
      els.push({ data: { id, kind: "activity", label: node.label, tip: [[t("model.activity"), node.label]] } });
    }
    if (parent) els.push({ data: { id: `e${id}`, source: parent, target: id, label: "" } });
  };
  walk(rep.root, null);
  return els;
}

function heuristicsElements(rep) {
  const els = [{ data: { id: "__s", kind: "marker", label: t("graph.start") } }, { data: { id: "__e", kind: "marker", label: t("graph.end") } }];
  for (const nd of rep.nodes) {
    els.push({ data: { id: `h:${nd.id}`, kind: "activity", label: `${nd.id}\n${fmtInt(nd.occurrences)}`,
      tip: [[t("model.activity"), nd.id], [t("graph.tip.occurrences"), fmtInt(nd.occurrences)]] } });
    if (nd.start) els.push({ data: { id: `s:${nd.id}`, source: "__s", target: `h:${nd.id}`, label: fmtInt(nd.start) } });
    if (nd.end) els.push({ data: { id: `x:${nd.id}`, source: `h:${nd.id}`, target: "__e", label: fmtInt(nd.end) } });
  }
  rep.edges.forEach((e, i) => els.push({ data: { id: `he${i}`, source: `h:${e.source}`, target: `h:${e.target}`,
    label: `${fmtNum(e.dependency, 2)} (${fmtInt(e.frequency)})`,
    tip: [[t("model.dependency"), fmtNum(e.dependency, 2)], [t("model.frequency"), fmtInt(e.frequency)]] } }));
  return els;
}

function bpmnElements(rep) {
  const els = rep.nodes.map((nd) => {
    if (nd.kind === "task") return { data: { id: nd.id, kind: "activity", label: nd.name, tip: [[t("model.bpmn_task"), nd.name]] } };
    if (GATEWAY_SYMBOL[nd.kind]) return { data: { id: nd.id, kind: "gateway", label: GATEWAY_SYMBOL[nd.kind], tip: [[t("model.gateway"), t(`model.${nd.kind}`)], [t("model.direction"), nd.direction || "—"]] } };
    if (nd.kind === "start_event" || nd.kind === "end_event") return { data: { id: nd.id, kind: nd.kind, label: "", tip: [[t(`model.${nd.kind}`), ""]] } };
    return { data: { id: nd.id, kind: "activity", label: nd.kind } };
  });
  rep.flows.forEach((f, i) => els.push({ data: { id: `f${i}`, source: f.source, target: f.target, label: "" } }));
  return els;
}

function tsElements(rep) {
  const els = rep.states.map((s) => ({ data: { id: s.id, kind: "state", label: s.name,
    tip: [[t("model.state"), s.name], [t("model.incoming"), s.incoming], [t("model.outgoing"), s.outgoing]] } }));
  rep.transitions.forEach((tr, i) => els.push({ data: { id: `t${i}`, source: tr.source, target: tr.target, label: tr.label } }));
  return els;
}

// Object types are told apart by colour (and listed in a legend next to the graph).
export function typeColor(types, type) {
  const vars = ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--accent", "--warn", "--danger", "--ok"];
  const i = Math.max(0, types.indexOf(type));
  return cssVar(vars[i % vars.length]);
}

function ocpnElements(rep) {
  const els = [];
  for (const p of rep.places) {
    const color = typeColor(rep.object_types, p.object_type);
    els.push({ data: { id: p.id, kind: "place", label: p.source ? fmtInt(p.initial) : "", initial: p.source, final: p.sink, color,
      tip: [[t("model.place"), p.name], [t("ocel.object_type"), p.object_type], [t("model.initial_tokens"), fmtInt(p.initial)],
        [t("model.final_tokens"), fmtInt(p.final)]] } });
  }
  for (const tr of rep.transitions) {
    els.push({ data: { id: tr.id, kind: tr.silent ? "silent" : "transition", label: tr.label || "τ",
      tip: [[tr.silent ? t("model.silent_transition") : t("model.transition"), tr.label || "τ"]] } });
  }
  rep.arcs.forEach((a, i) => els.push({ data: { id: `a${i}`, source: a.source, target: a.target, label: "",
    color: typeColor(rep.object_types, a.object_type), variable: a.variable,
    tip: [[t("ocel.object_type"), a.object_type], [t("ocel.arc_kind"), a.variable ? t("ocel.variable_arc") : t("ocel.single_arc")]] } }));
  return els;
}

function ocdfgElements(rep) {
  const els = rep.nodes.map((nd) => ({ data: { id: `n:${nd.id}`, kind: "activity", label: `${nd.id}\n${fmtInt(nd.unique_events)}`,
    tip: [[t("model.activity"), nd.id], [t("ocel.unique_events"), fmtInt(nd.unique_events)],
      ...Object.entries(nd.per_type).map(([ty, c]) => [t("ocel.events_with_type", { type: ty }), fmtInt(c)])] } }));
  rep.edges.forEach((e, i) => els.push({ data: { id: `e${i}`, source: `n:${e.source}`, target: `n:${e.target}`,
    label: fmtInt(e.object_transitions), color: typeColor(rep.object_types, e.object_type),
    tip: [[t("ocel.object_type"), e.object_type], [t("ocel.object_transitions"), fmtInt(e.object_transitions)],
      [t("ocel.objects"), fmtInt(e.objects)], [t("ocel.unique_event_pairs"), fmtInt(e.unique_event_pairs)],
      [t("ocel.median_elapsed"), fmtDur(e.median_elapsed_s)]] } }));
  return els;
}

function taskDfgElements(rep) {
  const els = [{ data: { id: "__s", kind: "marker", label: t("graph.start") } }, { data: { id: "__e", kind: "marker", label: t("graph.end") } }];
  for (const nd of rep.nodes) {
    els.push({ data: { id: `n:${nd.id}`, kind: "activity", label: `${nd.id}\n${fmtInt(nd.events)}`,
      tip: [[t("model.activity"), nd.id], [t("task.events"), fmtInt(nd.events)], [t("task.sessions"), fmtInt(nd.sessions)],
        [t("task.explicit_duration_mean"), nd.mean_explicit_duration_s === null ? t("task.no_explicit_duration") : fmtDur(nd.mean_explicit_duration_s)],
        [t("task.events_with_duration"), fmtInt(nd.events_with_duration)]] } });
  }
  rep.start.forEach((s, i) => els.push({ data: { id: `s${i}`, source: "__s", target: `n:${s.activity}`, label: fmtInt(s.sessions) } }));
  rep.end.forEach((s, i) => els.push({ data: { id: `x${i}`, source: `n:${s.activity}`, target: "__e", label: fmtInt(s.sessions) } }));
  rep.edges.forEach((e, i) => els.push({ data: { id: `e${i}`, source: `n:${e.source}`, target: `n:${e.target}`, label: fmtInt(e.count),
    tip: [[t("model.frequency"), fmtInt(e.count)], [t("task.sessions"), fmtInt(e.sessions)]] } }));
  return els;
}

function typeGraphElements(rep) {
  const types = rep.nodes.map((n) => n.object_type);
  const els = rep.nodes.map((n) => ({ data: { id: `t:${n.object_type}`, kind: "activity", color: typeColor(types, n.object_type),
    label: `${n.object_type}\n${fmtInt(n.objects)}`, tip: [[t("ocel.object_type"), n.object_type], [t("ocel.objects"), fmtInt(n.objects)],
      [t("ocel.unique_events"), fmtInt(n.unique_events)]] } }));
  rep.edges.forEach((e, i) => els.push({ data: { id: `s${i}`, source: `t:${e.source}`, target: `t:${e.target}`, kind: "shared",
    label: fmtInt(e.shared_events), tip: [[t("ocel.shared_events"), fmtInt(e.shared_events)], [t("rel.object_pairs"), fmtInt(e.object_pairs)]] } }));
  rep.o2o.forEach((e, i) => els.push({ data: { id: `o${i}`, source: `t:${e.source}`, target: `t:${e.target}`, kind: "o2o",
    label: `${e.qualifier || "—"} (${fmtInt(e.relationships)})`, tip: [[t("ocel.qualifier"), e.qualifier || "—"], [t("ocel.o2o"), fmtInt(e.relationships)]] } }));
  return els;
}

function colorStyle() {
  return [
    { selector: "node[color]", style: { "border-color": "data(color)" } },
    { selector: "edge[color]", style: { "line-color": "data(color)", "target-arrow-color": "data(color)" } },
    { selector: "edge[?variable]", style: { width: 4, "line-style": "solid", "line-opacity": 0.85 } },
    { selector: 'edge[kind = "shared"]', style: { "target-arrow-shape": "none", width: 3 } },
    { selector: 'edge[kind = "o2o"]', style: { "line-style": "dashed" } },
  ];
}

// --------------------------------------------------------------------------- graph view
export class ModelGraph {
  constructor(container) {
    this.container = container;
    this.cy = null;
  }

  destroy() {
    if (this.cy) { this.cy.destroy(); this.cy = null; }
  }

  show(rep) {
    this.destroy();
    let elements;
    let rankDir = "LR";
    if (rep.type === "petri_net") elements = petriElements(rep);
    else if (rep.type === "process_tree") { elements = treeElements(rep); rankDir = "TB"; }
    else if (rep.type === "heuristics_net") elements = heuristicsElements(rep);
    else if (rep.type === "bpmn") elements = bpmnElements(rep);
    else if (rep.type === "transition_system") elements = tsElements(rep);
    else if (rep.type === "oc_petri_net") elements = ocpnElements(rep);
    else if (rep.type === "object_centric_dfg") elements = ocdfgElements(rep);
    else if (rep.type === "task_dfg") elements = taskDfgElements(rep);
    else if (rep.type === "object_type_graph") elements = typeGraphElements(rep);
    else return false;
    this.cy = cytoscape({ container: this.container, elements, style: [...baseStyle(), ...colorStyle()], layout: layout(rankDir),
      wheelSensitivity: 0.25, minZoom: 0.1, maxZoom: 3 });
    const tip = document.getElementById("tooltip");
    this.cy.on("mouseover", "node, edge", (evt) => {
      const rows = evt.target.data("tip");
      if (!rows || !tip) return;
      clear(tip, h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])));
      tip.classList.remove("hidden");
      const e = evt.originalEvent;
      tip.style.left = `${Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)}px`;
      tip.style.top = `${Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8)}px`;
    });
    this.cy.on("mouseout pan zoom", () => tip?.classList.add("hidden"));
    requestAnimationFrame(() => { if (this.cy) { this.cy.resize(); this.cy.fit(undefined, 16); } });
    return true;
  }

  fit() { this.cy?.fit(undefined, 16); }

  zoom(f) {
    if (!this.cy) return;
    this.cy.zoom({ level: this.cy.zoom() * f, renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 } });
  }

  pngDataUrl() {
    return this.cy ? this.cy.png({ full: true, scale: 2, bg: cssVar("--surface") || "#ffffff" }) : null;
  }
}

// --------------------------------------------------------------------------- non-graph views
function powlNode(node) {
  if (node.kind === "activity") return h("span", { class: "step" }, node.label);
  if (node.kind === "silent") return h("span", { class: "step muted" }, "τ");
  if (node.kind === "partial_order") {
    return h("div", { class: "powl-po" },
      h("div", { class: "small muted" }, t("model.partial_order")),
      h("ol", { class: "powl-children" }, node.children.map((c) => h("li", {}, powlNode(c)))),
      node.order.length ? h("div", { class: "small" }, t("model.order"), " ", node.order.map(([a, b]) => `${a + 1} → ${b + 1}`).join(", ")) : h("div", { class: "small muted" }, t("model.no_order")));
  }
  return h("div", { class: "powl-op" }, h("b", {}, t(`model.op.${node.kind}`) || node.kind),
    h("ol", { class: "powl-children" }, (node.children || []).map((c) => h("li", {}, powlNode(c)))));
}

function declareView(rep) {
  const templates = [...new Set(rep.constraints.map((c) => c.template))];
  const box = h("div", {});
  const search = h("input", { type: "search", placeholder: t("model.search_activity") });
  const tmpl = h("select", {}, h("option", { value: "" }, t("model.all_templates")), templates.map((x) => h("option", { value: x }, x)));
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = rep.constraints.filter((c) => (!tmpl.value || c.template === tmpl.value) && (!q || c.activities.some((a) => a.toLowerCase().includes(q))));
    clear(tableBox, table([
      { key: "template", label: t("model.template"), render: (c) => h("code", {}, c.template) },
      { key: "activities", label: t("model.activities"), render: (c) => c.activities.join(" → ") },
      { key: "support", label: t("model.support"), num: true, render: (c) => fmtInt(c.support) },
      { key: "confidence", label: t("model.confidence"), num: true, render: (c) => fmtInt(c.confidence) },
    ], rows.slice(0, 500)), rows.length > 500 ? h("p", { class: "small muted" }, t("model.first_rows", { n: 500, total: fmtInt(rows.length) })) : null);
  };
  const tableBox = h("div", {});
  search.addEventListener("input", draw);
  tmpl.addEventListener("change", draw);
  clear(box, h("div", { class: "actions" }, tmpl, search), tableBox);
  draw();
  return box;
}

function logSkeletonView(rep) {
  const rel = Object.entries(rep.relations).map(([name, pairs]) => h("details", { open: pairs.length <= 20 },
    h("summary", {}, `${t(`model.ls.${name}`)} (${fmtInt(pairs.length)})`),
    pairs.length ? table([{ key: "a", label: "A", render: (p) => p[0] }, { key: "b", label: "B", render: (p) => p[1] }], pairs) : h("p", { class: "muted small" }, "—")));
  const freq = table([
    { key: "a", label: t("model.activity"), render: (r) => r[0] },
    { key: "f", label: t("model.allowed_counts"), render: (r) => r[1].join(", ") },
  ], Object.entries(rep.activ_freq));
  return h("div", {}, rel, h("h3", {}, t("model.ls.activ_freq")), freq);
}

function temporalView(rep) {
  return table([
    { key: "source", label: t("model.from") },
    { key: "target", label: t("model.to") },
    { key: "mean", label: t("stat.mean"), num: true, render: (p) => fmtDur(p.mean_s) },
    { key: "std", label: t("model.std_dev"), num: true, render: (p) => fmtDur(p.std_s) },
  ], rep.pairs);
}

/**
 * Render a representation into ``host``. Returns the ModelGraph (for PNG export) or null.
 */
export function renderModel(host, rep, { height = 520 } = {}) {
  const prev = host._modelGraph;
  if (prev) prev.destroy();
  host._modelGraph = null;
  if (["petri_net", "process_tree", "heuristics_net", "bpmn", "transition_system", "oc_petri_net", "object_centric_dfg", "task_dfg", "object_type_graph"].includes(rep.type)) {
    const canvas = h("div", { class: "graph", style: { height: `${height}px` } });
    const controls = h("div", { class: "graph-controls" });
    clear(host, h("div", { class: "graph-wrap" }, canvas, controls));
    const g = new ModelGraph(canvas);
    g.show(rep);
    clear(controls,
      h("button", { type: "button", title: t("proc.zoom_in"), "aria-label": t("proc.zoom_in"), onclick: () => g.zoom(1.25) }, "+"),
      h("button", { type: "button", title: t("proc.zoom_out"), "aria-label": t("proc.zoom_out"), onclick: () => g.zoom(0.8) }, "−"),
      h("button", { type: "button", title: t("proc.fit"), "aria-label": t("proc.fit"), onclick: () => g.fit() }, "⤢"));
    host._modelGraph = g;
    if (rep.type === "process_tree" && rep.text) host.append(h("p", { class: "small mono", style: { marginTop: "8px" } }, rep.text));
    if (rep.object_types) host.append(typeLegend(rep.object_types, rep.type === "oc_petri_net"));
    return g;
  }
  if (rep.type === "powl") clear(host, h("div", { class: "powl" }, powlNode(rep.root)), h("p", { class: "small mono" }, rep.text));
  else if (rep.type === "declare") clear(host, declareView(rep));
  else if (rep.type === "log_skeleton") clear(host, logSkeletonView(rep));
  else if (rep.type === "temporal_profile") clear(host, temporalView(rep));
  else clear(host, h("pre", {}, JSON.stringify(rep, null, 2)));
  return null;
}

export function typeLegend(types, arcs = false) {
  return h("div", { class: "type-legend small" }, types.map((ty) => h("span", { class: "legend-item" },
    h("i", { class: "swatch", style: { background: typeColor(types, ty) } }), ty)),
  arcs ? h("span", { class: "muted" }, t("ocel.variable_arc_legend")) : null);
}

export function sizeText(type, size) {
  if (!size) return "—";
  return Object.entries(size).map(([k, v]) => `${t(`model.size.${k}`)}: ${fmtInt(v)}`).join(" · ");
}

export function downloadPng(graph, name) {
  const url = graph?.pngDataUrl();
  if (!url) return;
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
}
