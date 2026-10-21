import {
  $, api, applyFilters, badge, bootPage, clear, currentDatasetId, exportLinks, filterParams, fmtDur, fmtInt, fmtPct, h,
  linkWith, loading, noDatasetState, pageParam, pager, readFilters, reveal, setUrl, showError, STATUS_LABEL, t, table,
  variantSequence,
} from "./core.js";
import { ProcessGraph } from "./graph.js";

const root = $("#variants-root");
const graphEl = $("#variant-graph");
const graph = new ProcessGraph(graphEl, {});
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
        { key: "case_id", label: t("common.case"), render: (c) => h("a", { href: linkWith("/cases", { extra: { case: c.case_id } }) }, c.case_id) },
        { key: "status", label: t("common.status"), render: (c) => badge(STATUS_LABEL[c.status], c.status) },
        { key: "dur", label: t("common.observed_duration"), num: true, render: (c) => fmtDur(c.observed_duration_s) },
      ], d.items),
      pager({ page: d.page, pageSize: d.page_size, total: d.total }, (p) => loadCases(v, p)));
  } catch (err) { showError(box, err); }
}

function select(v) {
  selected = v;
  setUrl({ extra: { select: v.variant_id } }, { replace: true });
  for (const tr of root.querySelectorAll("tbody tr")) tr.classList.toggle("selected", tr.dataset.vid === v.variant_id);
  graph.highlightPath(v.activities);
  $("#path-note").textContent = t("var.path_note", { rank: v.rank, cases: fmtInt(v.case_count), share: fmtPct(v.share) });
  clear($("#variant-actions"), h("button", { type: "button", class: "btn btn-sm", onclick: () => {
    const f = readFilters();
    applyFilters({ ...f, variant: [v.variant_id] });
  } }, t("var.apply_filter")));
  loadCases(v);
}

async function render() {
  const ds = currentDatasetId();
  graph.setData(null);
  if (!ds) { clear($("#variant-exports")); return noDatasetState(root); }
  loading(root, t("var.computing"));
  let res, gres;
  try {
    [res, gres] = await Promise.all([
      api(`/api/datasets/${ds}/variants`, { params: (() => { const p = filterParams(); p.set("limit", limit); return p; })() }),
      api(`/api/datasets/${ds}/graph`, { params: filterParams() }),
    ]);
  } catch (err) { return showError(root, err); }
  clear($("#variant-exports"), exportLinks(ds, [["variants", "csv", t("export.variants_csv")], ["variants", "json", t("export.variants_json")]]));
  const d = res.data;
  if (!d.total) {
    clear($("#variant-cases"), h("div", { class: "state" }, t("var.none")));
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, t("empty.no_match.title")), t("empty.no_match.hint")));
  }
  clear(root,
    h("p", { class: "small muted" }, t("var.summary", { variants: fmtInt(d.total), cases: fmtInt(d.case_count) })),
    table([
      { key: "rank", label: "#", num: true },
      { key: "seq", label: t("var.sequence"), render: (v) => variantSequence(v.activities) },
      { key: "case_count", label: t("common.cases"), num: true, render: (v) => fmtInt(v.case_count) },
      { key: "share", label: t("var.share"), num: true, render: (v) => h("div", {}, fmtPct(v.share), h("div", { class: "bar" }, h("i", { style: { width: `${v.share * 100}%` } }))) },
      { key: "cum", label: t("var.cumulative"), num: true, render: (v) => fmtPct(v.cumulative_share) },
      { key: "median", label: t("var.median_observed"), num: true, render: (v) => fmtDur(v.observed_duration.median) },
      { key: "p90", label: t("var.p90_observed"), num: true, render: (v) => fmtDur(v.observed_duration.p90) },
      { key: "rework", label: t("common.rework"), render: (v) => v.has_rework ? badge(t("var.repeats", { n: v.repeated_activities }), "warn") : badge(t("var.no_rework")) },
    ], d.items, { onRowClick: (v) => select(v) }),
    d.total > d.items.length ? h("div", { class: "actions", style: { marginTop: "8px" } },
      h("span", { class: "muted small" }, t("var.showing", { shown: d.items.length, total: fmtInt(d.total) })),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => { limit += 50; render(); } }, t("var.show_more"))) : null);
  reveal(root.querySelector("tbody"), { stagger: 25 });
  root.querySelectorAll("tbody tr").forEach((tr, i) => { tr.dataset.vid = d.items[i].variant_id; });
  graph.setData(gres.data);
  const pre = pageParam("select");
  const want = d.items.find((v) => v.variant_id === pre) || null;
  if (want) select(want);
  else { selected = null; clear($("#variant-cases"), h("div", { class: "state" }, t("var.select_cases_hint"))); }
}

bootPage(render);
