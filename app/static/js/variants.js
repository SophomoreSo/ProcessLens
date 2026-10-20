import {
  $, api, applyFilters, badge, bootPage, clear, currentDatasetId, exportLinks, filterParams, fmtDur, fmtInt, fmtPct, h,
  linkWith, loading, noDatasetState, pageParam, pager, readFilters, setUrl, showError, STATUS_LABEL, table,
  variantSequence,
} from "./core.js";
import { ProcessGraph } from "./graph.js";

const root = $("#variants-root");
const graphEl = $("#variant-graph");
const graph = new ProcessGraph(graphEl, {});
let variantsData = [];
let selected = null;
let limit = 25;

async function loadCases(v, page = 1) {
  const box = $("#variant-cases");
  loading(box);
  const params = filterParams({ ...readFilters(), variant: [v.variant_id] });
  params.set("page", page);
  params.set("page_size", 10);
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/cases`, { params });
    const d = res.data;
    clear(box,
      table([
        { key: "case_id", label: "Case", render: (c) => h("a", { href: linkWith("/cases", { extra: { case: c.case_id } }) }, c.case_id) },
        { key: "status", label: "Status", render: (c) => badge(STATUS_LABEL[c.status], c.status) },
        { key: "dur", label: "Observed duration", num: true, render: (c) => fmtDur(c.observed_duration_s) },
      ], d.items),
      pager({ page: d.page, pageSize: d.page_size, total: d.total }, (p) => loadCases(v, p)));
  } catch (err) { showError(box, err); }
}

function select(v) {
  selected = v;
  setUrl({ extra: { select: v.variant_id } }, { replace: true });
  for (const tr of root.querySelectorAll("tbody tr")) tr.classList.toggle("selected", tr.dataset.vid === v.variant_id);
  graph.highlightPath(v.activities);
  $("#path-note").textContent = `Variant #${v.rank}: ${fmtInt(v.case_count)} cases (${fmtPct(v.share)})`;
  clear($("#variant-actions"), h("button", { type: "button", class: "btn btn-sm", onclick: () => {
    const f = readFilters();
    applyFilters({ ...f, variant: [v.variant_id] });
  } }, "Apply as global filter"));
  loadCases(v);
}

async function render() {
  const ds = currentDatasetId();
  graph.setData(null);
  if (!ds) { clear($("#variant-exports")); return noDatasetState(root); }
  loading(root, "Computing variants…");
  let res, gres;
  try {
    [res, gres] = await Promise.all([
      api(`/api/datasets/${ds}/variants`, { params: (() => { const p = filterParams(); p.set("limit", limit); return p; })() }),
      api(`/api/datasets/${ds}/graph`, { params: filterParams() }),
    ]);
  } catch (err) { return showError(root, err); }
  clear($("#variant-exports"), exportLinks(ds, [["variants", "csv", "Variants CSV"], ["variants", "json", "Variants JSON"]]));
  const d = res.data;
  variantsData = d.items;
  if (!d.total) {
    clear($("#variant-cases"), h("div", { class: "state" }, "No variants."));
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, "No cases match the current filters"), "Remove a filter to widen the selection."));
  }
  clear(root,
    h("p", { class: "small muted" }, `${fmtInt(d.total)} variants across ${fmtInt(d.case_count)} cases. Repeated activities are highlighted. Shares are of the filtered cases.`),
    table([
      { key: "rank", label: "#", num: true },
      { key: "seq", label: "Activity sequence", render: (v) => variantSequence(v.activities) },
      { key: "case_count", label: "Cases", num: true, render: (v) => fmtInt(v.case_count) },
      { key: "share", label: "Share", num: true, render: (v) => h("div", {}, fmtPct(v.share), h("div", { class: "bar" }, h("i", { style: { width: `${v.share * 100}%` } }))) },
      { key: "cum", label: "Cumulative", num: true, render: (v) => fmtPct(v.cumulative_share) },
      { key: "median", label: "Median observed", num: true, render: (v) => fmtDur(v.observed_duration.median) },
      { key: "p90", label: "p90 observed", num: true, render: (v) => fmtDur(v.observed_duration.p90) },
      { key: "rework", label: "Rework", render: (v) => v.has_rework ? badge(`${v.repeated_activities} repeat${v.repeated_activities > 1 ? "s" : ""}`, "warn") : badge("none") },
    ], d.items, { onRowClick: (v) => select(v) }),
    d.total > d.items.length ? h("div", { class: "actions", style: { marginTop: "8px" } },
      h("span", { class: "muted small" }, `Showing ${d.items.length} of ${fmtInt(d.total)} variants.`),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => { limit += 50; render(); } }, "Show more")) : null);
  root.querySelectorAll("tbody tr").forEach((tr, i) => { tr.dataset.vid = d.items[i].variant_id; });
  graph.setData(gres.data);
  const pre = pageParam("select");
  const want = d.items.find((v) => v.variant_id === pre) || null;
  if (want) select(want);
  else { selected = null; clear($("#variant-cases"), h("div", { class: "state" }, "Select a variant to list its cases.")); }
}

bootPage(render);
