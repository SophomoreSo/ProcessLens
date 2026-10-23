// Object relationships: how object types are connected, and how relationships relate to object lifetimes.
import {
  $, api, bootPage, clear, currentDatasetId, currentObjectType, fmtDur, fmtInt, h, noDatasetState, perspectiveLink,
  showError, t, table,
} from "./core.js";
import { renderModel } from "./models.js";

async function drawTypeGraph(ds) {
  const g = await api(`/api/datasets/${ds}/ocel/type-graph`);
  if (g.nodes.length < 2) clear($("#type-graph"), h("p", { class: "muted" }, t("rel.single_type")));
  else renderModel($("#type-graph"), g, { height: 380 });
  clear($("#type-tables"),
    h("div", {}, h("h3", {}, t("rel.shared_events")), g.edges.length ? table([
      { key: "pair", label: t("rel.types"), render: (e) => `${e.source} – ${e.target}` },
      { key: "events", label: t("ocel.shared_events"), num: true, render: (e) => fmtInt(e.shared_events) },
      { key: "pairs", label: t("rel.object_pairs"), num: true, render: (e) => fmtInt(e.object_pairs) },
    ], g.edges) : h("p", { class: "small muted" }, "—")),
    h("div", {}, h("h3", {}, t("ocel.o2o_qualifiers")), g.o2o.length ? table([
      { key: "s", label: t("ocel.source_type"), render: (r) => r.source },
      { key: "q", label: t("ocel.qualifier"), render: (r) => r.qualifier || "—" },
      { key: "tt", label: t("ocel.target_type"), render: (r) => r.target },
      { key: "c", label: t("ocel.count"), num: true, render: (r) => fmtInt(r.relationships) },
    ], g.o2o) : h("p", { class: "small muted" }, "—")));
}

async function drawDelays(ds) {
  const ty = currentObjectType();
  $("#delays-title").textContent = t("rel.delays_for", { type: ty });
  const r = await api(`/api/datasets/${ds}/ocel/relationship-delays`, { params: { object_type: ty } });
  clear($("#relations-view"), h("p", { class: "small" }, t("ocel.relations_summary", { n: fmtInt(r.objects), type: ty, median: fmtDur(r.median_lifetime_s) })),
    r.groups.length ? r.groups.map((g) => h("div", { style: { marginTop: "10px" } },
      h("h4", {}, t(`ocel.via.${g.via}`, { type: g.related_type })),
      table([
        { key: "related", label: t("ocel.related_objects"), render: (b) => b.related_label },
        { key: "objects", label: t("ocel.objects"), num: true, render: (b) => fmtInt(b.objects) },
        { key: "median", label: t("ocel.median_lifetime"), num: true, render: (b) => fmtDur(b.median_lifetime_s) },
        { key: "p90", label: t("ocel.p90_lifetime"), num: true, render: (b) => fmtDur(b.p90_lifetime_s) },
      ], g.buckets))) : h("p", { class: "muted" }, t("ocel.no_relations")),
    h("p", {}, h("a", { class: "btn btn-sm", href: perspectiveLink("/guide", ty, { q: "object_relationship_delays" }) }, t("rel.ask_guide"))));
}

async function render() {
  const ds = currentDatasetId();
  if (!ds) return noDatasetState($("#type-graph"));
  try { await drawTypeGraph(ds); } catch (err) { showError($("#type-graph"), err); }
  try { await drawDelays(ds); } catch (err) { showError($("#relations-view"), err); }
}

bootPage(render, { filters: false });
