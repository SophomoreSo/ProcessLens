// Directly-follows graph rendering with Cytoscape.js (+ dagre layout).
// Visibility thresholds only hide elements in the drawing; they never change
// the analysed population or any KPI.
/* global cytoscape, cytoscapeDagre */
import { cssVar } from "./charts.js";
import { $, clear, fmtDur, fmtInt, fmtPct, h } from "./core.js";

if (typeof cytoscape !== "undefined" && typeof cytoscapeDagre !== "undefined") {
  try { cytoscape.use(cytoscapeDagre); } catch { /* already registered */ }
}

const START = "__start__";
const END = "__end__";

function tooltipShow(evt, rows, title) {
  const tip = $("#tooltip");
  clear(tip, h("strong", {}, title), h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
  tip.classList.remove("hidden");
  const e = evt.originalEvent;
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = Math.min(e.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}

function tooltipHide() {
  $("#tooltip")?.classList.add("hidden");
}

export class ProcessGraph {
  /**
   * @param {HTMLElement} container
   * @param {object} opts { onNodeClick(activity), onEdgeClick(source, target) }
   */
  constructor(container, opts = {}) {
    this.container = container;
    this.opts = opts;
    this.cy = null;
    this.data = null;
    this.mode = "frequency";
    this.metric = "median";
    this.minNodeShare = 0;
    this.minEdgeShare = 0;
  }

  setData(data) {
    this.data = data;
    this.draw();
  }

  setMode(mode, metric) {
    this.mode = mode;
    if (metric) this.metric = metric;
    this.restyle();
  }

  setThresholds(minNodeShare, minEdgeShare) {
    this.minNodeShare = minNodeShare;
    this.minEdgeShare = minEdgeShare;
    this.applyVisibility();
  }

  edgeLabel(d) {
    if (this.mode === "frequency") return `${fmtInt(d.occurrences)}`;
    if (!d.elapsed) return "";
    return fmtDur(d.elapsed[this.metric]);
  }

  draw() {
    const d = this.data;
    if (this.cy) { this.cy.destroy(); this.cy = null; }
    if (!d || !d.nodes.length) return;
    const total = d.case_count || 1;
    const maxOcc = Math.max(1, ...d.edges.map((e) => e.occurrences));
    const maxNode = Math.max(1, ...d.nodes.map((n) => n.occurrences));
    const elements = [
      { data: { id: START, label: "▶ Start", kind: "marker", share: 1 } },
      { data: { id: END, label: "■ End", kind: "marker", share: 1 } },
      ...d.nodes.map((n) => ({
        data: { id: `n:${n.id}`, activity: n.id, label: `${n.label}\n${fmtInt(n.occurrences)}`, kind: "activity",
          occurrences: n.occurrences, case_count: n.case_count, share: n.case_count / total,
          weight: n.occurrences / maxNode, execution: n.execution },
      })),
      ...d.edges.map((e, i) => ({
        data: {
          id: `e${i}`, source: e.source === START ? START : `n:${e.source}`, target: e.target === END ? END : `n:${e.target}`,
          src: e.source, tgt: e.target, occurrences: e.occurrences, case_count: e.case_count, elapsed: e.elapsed,
          share: e.case_count / total, weight: e.occurrences / maxOcc,
        },
      })),
    ];
    this.cy = cytoscape({
      container: this.container,
      elements,
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 3,
      style: this.style(),
      layout: this.layoutOptions(),
    });
    this.bind();
    this.restyle();
    this.applyVisibility();
  }

  layoutOptions() {
    if (typeof cytoscapeDagre !== "undefined") {
      return { name: "dagre", rankDir: "TB", nodeSep: 40, rankSep: 60, edgeSep: 12, padding: 20 };
    }
    return { name: "breadthfirst", directed: true, roots: `#${START}`, padding: 20, spacingFactor: 1.1 };
  }

  style() {
    const accent = cssVar("--accent");
    const text = cssVar("--text");
    const surface = cssVar("--surface");
    const border = cssVar("--border-strong");
    const muted = cssVar("--text-muted");
    return [
      { selector: "node", style: {
        label: "data(label)", "text-wrap": "wrap", "text-max-width": 150, "text-valign": "center", "text-halign": "center",
        "font-size": 11, color: text, shape: "round-rectangle", width: 170, height: 46, "background-color": surface,
        "border-width": 2, "border-color": accent,
      } },
      { selector: 'node[kind = "marker"]', style: {
        shape: "ellipse", width: 74, height: 30, "background-color": muted, color: surface, "border-width": 0, "font-weight": "bold",
      } },
      { selector: "edge", style: {
        "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.9, label: "data(display)",
        "font-size": 10, color: text, "text-background-color": surface, "text-background-opacity": 0.85,
        "text-background-padding": 2, "line-color": border, "target-arrow-color": border,
        width: "mapData(weight, 0, 1, 1, 9)",
      } },
      { selector: "edge.slow", style: { "line-color": cssVar("--chart-3"), "target-arrow-color": cssVar("--chart-3") } },
      { selector: ".faded", style: { opacity: 0.15 } },
      { selector: ".highlight", style: { "border-color": cssVar("--accent-2"), "line-color": cssVar("--accent-2"),
        "target-arrow-color": cssVar("--accent-2"), "border-width": 4, opacity: 1 } },
      { selector: "node:selected", style: { "border-width": 4 } },
    ];
  }

  restyle() {
    if (!this.cy) return;
    const elapsed = this.cy.edges().map((e) => e.data("elapsed")?.[this.metric] ?? 0);
    const maxElapsed = Math.max(1, ...elapsed);
    this.cy.batch(() => {
      this.cy.edges().forEach((e) => {
        const d = e.data();
        e.data("display", this.edgeLabel(d));
        if (this.mode === "elapsed") {
          const v = d.elapsed ? d.elapsed[this.metric] : 0;
          e.data("weight", v / maxElapsed);
          e.toggleClass("slow", v / maxElapsed > 0.5);
        } else {
          e.data("weight", d.occurrences / Math.max(1, ...this.data.edges.map((x) => x.occurrences)));
          e.removeClass("slow");
        }
      });
    });
    this.cy.style(this.style());
  }

  applyVisibility() {
    if (!this.cy) return;
    this.cy.batch(() => {
      this.cy.nodes('[kind = "activity"]').forEach((n) => n.style("display", n.data("share") >= this.minNodeShare ? "element" : "none"));
      this.cy.edges().forEach((e) => {
        const visible = e.data("share") >= this.minEdgeShare && e.source().style("display") !== "none" && e.target().style("display") !== "none";
        e.style("display", visible ? "element" : "none");
      });
    });
    const shownN = this.cy.nodes('[kind = "activity"]').filter((n) => n.style("display") !== "none").length;
    const shownE = this.cy.edges().filter((e) => e.style("display") !== "none").length;
    this.opts.onVisibility?.({ shownNodes: shownN, totalNodes: this.data.nodes.length, shownEdges: shownE, totalEdges: this.data.edges.length });
  }

  bind() {
    const cy = this.cy;
    cy.on("mouseover", "node", (evt) => {
      const d = evt.target.data();
      if (d.kind === "marker") return tooltipShow(evt, [["", d.id === START ? "Synthetic start marker" : "Synthetic end marker"]], d.label);
      const rows = [["Occurrences", fmtInt(d.occurrences)], ["Cases", `${fmtInt(d.case_count)} (${fmtPct(d.share)})`]];
      if (d.execution) rows.push(["Median execution", fmtDur(d.execution.median)], ["Execution n", fmtInt(d.execution.n)]);
      rows.push(["", "Click to list cases with this activity"]);
      tooltipShow(evt, rows, d.activity);
    });
    cy.on("mouseover", "edge", (evt) => {
      const d = evt.target.data();
      const name = (s) => (s === START ? "▶ Start" : s === END ? "■ End" : s);
      const rows = [["Occurrences", fmtInt(d.occurrences)], ["Distinct cases", `${fmtInt(d.case_count)} (${fmtPct(d.share)})`]];
      if (d.elapsed) rows.push(["Median elapsed", fmtDur(d.elapsed.median)], ["p90 elapsed", fmtDur(d.elapsed.p90)], ["Mean elapsed", fmtDur(d.elapsed.mean)]);
      else rows.push(["Elapsed", "n/a for start/end markers"]);
      rows.push(["", "Click to list matching cases"]);
      tooltipShow(evt, rows, `${name(d.src)} → ${name(d.tgt)}`);
    });
    cy.on("mouseout", tooltipHide);
    cy.on("pan zoom", tooltipHide);
    cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      if (d.kind === "activity") this.opts.onNodeClick?.(d.activity);
    });
    cy.on("tap", "edge", (evt) => {
      const d = evt.target.data();
      this.opts.onEdgeClick?.(d.src, d.tgt);
    });
  }

  highlightPath(activities) {
    if (!this.cy) return;
    this.cy.elements().removeClass("highlight faded");
    if (!activities || !activities.length) return;
    const ids = new Set();
    const nodeId = (a) => (a === START || a === END ? a : `n:${a}`);
    const seq = [START, ...activities, END];
    for (let i = 0; i < seq.length; i++) {
      ids.add(nodeId(seq[i]));
      if (i) {
        const edge = this.cy.edges().filter((e) => e.data("source") === nodeId(seq[i - 1]) && e.data("target") === nodeId(seq[i]));
        edge.forEach((e) => ids.add(e.id()));
      }
    }
    this.cy.elements().forEach((el) => el.addClass(ids.has(el.id()) ? "highlight" : "faded"));
  }

  zoom(factor) {
    if (!this.cy) return;
    const c = { x: this.cy.width() / 2, y: this.cy.height() / 2 };
    this.cy.zoom({ level: this.cy.zoom() * factor, renderedPosition: c });
  }

  reset() {
    if (!this.cy) return;
    this.cy.elements().removeClass("highlight faded");
    this.cy.layout(this.layoutOptions()).run();
    this.cy.fit(undefined, 20);
  }

  fit() { this.cy?.fit(undefined, 20); }
}
