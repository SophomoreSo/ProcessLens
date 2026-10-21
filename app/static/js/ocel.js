// Object-centric event logs: import, unique counts, OC-DFG, objects, relationships, OCPN and projection.
import { paramInputs, readParams } from "./algoforms.js";
import {
  $, $$, api, badge, bootPage, clear, countUp, fmtDur, fmtInt, fmtNum, fmtTs, h, reveal, showError, t, table, toast,
} from "./core.js";
import { followJob } from "./jobs.js";
import { renderModel, typeColor } from "./models.js";
import { errorText, followRun, provenance, statusBadge } from "./runs.js";

let logs = [];
let current = null;
let selectedTypes = [];
let ocAlgorithms = [];
let tab = "ocdfg";
let objPage = 1;

const params = () => new URLSearchParams(location.search);
const typeParams = () => (selectedTypes.length ? { object_type: selectedTypes } : {});
const allTypes = () => (current ? current.object_types.map((x) => x.name) : []);

function setUrl() {
  const p = new URLSearchParams();
  if (current) p.set("log", current.id);
  if (selectedTypes.length) p.set("types", selectedTypes.join(","));
  if (tab !== "ocdfg") p.set("tab", tab);
  history.replaceState(null, "", `${location.pathname}${p.toString() ? `?${p}` : ""}`);
}

function findingsBlock(findings) {
  if (!findings?.length) return h("p", { class: "small muted" }, t("ocel.no_findings"));
  return h("details", {}, h("summary", {}, t("ocel.findings_count", { n: fmtInt(findings.length) })),
    table([
      { key: "severity", label: t("ocel.severity"), render: (f) => badge(t(`severity.${f.severity}`), f.severity) },
      { key: "element", label: t("ocel.element"), render: (f) => h("code", {}, f.element || "—") },
      { key: "message", label: t("ocel.message"), render: (f) => t(f.key, f.params || {}) },
    ], findings.slice(0, 200)));
}

async function loadLogs(selectId) {
  logs = (await api("/api/ocel")).items;
  const sel = $("#log-select");
  if (!logs.length) {
    clear(sel, h("option", { value: "" }, t("ocel.no_logs")));
    current = null;
    $("#ocel-body").classList.add("hidden");
    clear($("#log-info"), h("p", { class: "muted" }, t("ocel.empty_hint")));
    return;
  }
  clear(sel, logs.map((l) => h("option", { value: l.id }, `${l.name} (#${l.id})`)));
  const want = Number(selectId || params().get("log")) || logs[0].id;
  current = logs.find((l) => l.id === want) || logs[0];
  sel.value = current.id;
}

function drawInfo() {
  const c = current.counts;
  clear($("#log-info"),
    h("div", { class: "chips" }, current.is_demo ? badge(t("ocel.demo_badge"), "synthetic") : null,
      badge(`${t("ocel.object_types")}: ${fmtInt(c.object_types)}`), badge(`${t("ocel.event_types")}: ${fmtInt(c.event_types)}`)),
    h("p", { class: "muted" }, current.description || ""),
    h("p", { class: "muted" }, t("ocel.source_line", { file: current.source_filename, sha: current.source_sha256.slice(0, 12),
      when: fmtTs(current.imported_at, "UTC") })),
    findingsBlock(current.findings));
}

function drawTypeFilter() {
  const types = allTypes();
  selectedTypes = selectedTypes.filter((x) => types.includes(x));
  clear($("#type-filter"), types.map((ty) => h("label", { class: "inline" },
    h("input", { type: "checkbox", value: ty, checked: selectedTypes.includes(ty), onchange: () => {
      selectedTypes = $$("#type-filter input:checked").map((i) => i.value);
      setUrl();
      refresh();
    } }), h("i", { class: "swatch", style: { background: typeColor(types, ty), width: "10px", height: "10px", borderRadius: "50%", display: "inline-block" } }), ty)));
  for (const sel of ["#obj-type", "#rel-type", "#proj-type"]) {
    const el = $(sel);
    const prev = el.value;
    clear(el, sel === "#obj-type" ? h("option", { value: "" }, t("ocel.all_types")) : null, types.map((ty) => h("option", { value: ty }, ty)));
    if (types.includes(prev)) el.value = prev;
  }
}

function kpi(label, value, sub, fmt = fmtInt) {
  const v = h("div", { class: "value" }, fmt(value));
  if (typeof value === "number") countUp(v, value, fmt);
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), v, sub ? h("div", { class: "sub" }, sub) : null);
}

async function drawStats() {
  const s = await api(`/api/ocel/${current.id}/stats`, { params: typeParams() });
  clear($("#kpis"),
    kpi(t("ocel.unique_events"), s.unique_events),
    kpi(t("ocel.unique_objects"), s.unique_objects),
    kpi(t("ocel.e2o"), s.e2o_relationships, t("ocel.e2o_sub")),
    kpi(t("ocel.o2o"), s.o2o_relationships),
    kpi(t("ocel.shared_events"), s.shared_events, t("ocel.shared_events_sub")),
    kpi(t("ocel.cost_unique"), s.cost_total_unique_events, t("ocel.cost_unique_sub"), (v) => (v === null || v === undefined ? "—" : fmtNum(v, 2))));
  reveal($("#kpis"));
  clear($("#types-view"),
    h("h3", {}, t("ocel.per_object_type")),
    table([
      { key: "object_type", label: t("ocel.object_type"), render: (r) => h("span", {}, h("i", { class: "swatch", style: { background: typeColor(allTypes(), r.object_type), width: "10px", height: "10px", borderRadius: "50%", display: "inline-block", marginRight: "6px" } }), r.object_type) },
      { key: "objects", label: t("ocel.objects"), num: true, render: (r) => fmtInt(r.objects) },
      { key: "unique_events", label: t("ocel.unique_events"), num: true, render: (r) => fmtInt(r.unique_events) },
      { key: "e2o", label: t("ocel.e2o"), num: true, render: (r) => fmtInt(r.e2o_relationships) },
      { key: "multi", label: t("ocel.events_several_objects"), num: true, render: (r) => fmtInt(r.events_with_several_objects_of_type) },
      { key: "median", label: t("ocel.median_events_per_object"), num: true, render: (r) => fmtNum(r.median_events_per_object, 1) },
    ], s.object_types),
    h("h3", {}, t("ocel.per_event_type")),
    table([
      { key: "event_type", label: t("ocel.event_type") },
      { key: "unique_events", label: t("ocel.unique_events"), num: true, render: (r) => fmtInt(r.unique_events) },
      { key: "e2o", label: t("ocel.e2o"), num: true, render: (r) => fmtInt(r.e2o_relationships) },
      { key: "types", label: t("ocel.object_types"), render: (r) => r.object_types.join(", ") },
    ], s.event_types),
    h("h3", {}, t("ocel.o2o_qualifiers")),
    s.o2o_qualifiers.length ? table([
      { key: "s", label: t("ocel.source_type"), render: (r) => r.source_type },
      { key: "q", label: t("ocel.qualifier"), render: (r) => r.qualifier || "—" },
      { key: "tt", label: t("ocel.target_type"), render: (r) => r.target_type },
      { key: "c", label: t("ocel.count"), num: true, render: (r) => fmtInt(r.count) },
    ], s.o2o_qualifiers) : h("p", { class: "muted small" }, "—"));
}

async function drawOcdfg() {
  try {
    const g = await api(`/api/ocel/${current.id}/ocdfg`, { params: typeParams() });
    if (!g.edges.length && !g.nodes.length) { clear($("#ocdfg-view"), h("p", { class: "muted" }, t("ocel.empty_selection"))); return; }
    renderModel($("#ocdfg-view"), g, { height: 560 });
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

async function drawObjects() {
  const box = $("#objects-view");
  try {
    const res = await api(`/api/ocel/${current.id}/objects`, { params: { object_type: $("#obj-type").value || undefined,
      search: $("#obj-search").value || undefined, page: objPage, page_size: 25 } });
    const pages = Math.max(1, Math.ceil(res.total / res.page_size));
    clear(box, table([
      { key: "object_id", label: t("ocel.object"), render: (r) => h("button", { type: "button", class: "obj-link", onclick: () => showObject(r.object_id) }, r.object_id) },
      { key: "object_type", label: t("ocel.object_type") },
      { key: "events", label: t("ocel.events"), num: true, render: (r) => fmtInt(r.events) },
      { key: "first", label: t("ocel.first_event"), render: (r) => (r.first ? fmtTs(r.first, current.display_timezone || "UTC") : "—") },
    ], res.items),
    h("div", { class: "actions small" },
      h("button", { type: "button", class: "btn btn-sm", disabled: objPage <= 1, onclick: () => { objPage -= 1; drawObjects(); } }, "‹"),
      t("ocel.page_of", { page: fmtInt(objPage), pages: fmtInt(pages) }),
      h("button", { type: "button", class: "btn btn-sm", disabled: objPage >= pages, onclick: () => { objPage += 1; drawObjects(); } }, "›"),
      h("span", { class: "muted" }, t("ocel.objects_total", { n: fmtInt(res.total) }))));
  } catch (err) { showError(box, err); }
}

async function showObject(objectId) {
  const box = $("#object-detail");
  try {
    const d = await api(`/api/ocel/${current.id}/objects/${encodeURIComponent(objectId)}`);
    const tz = current.display_timezone || "UTC";
    const link = (o) => h("button", { type: "button", class: "obj-link", onclick: () => showObject(o.object_id) }, o.object_id);
    clear(box,
      h("h3", {}, `${d.object_id} `, badge(d.object_type)),
      h("p", { class: "small muted" }, t("ocel.object_summary", { n: fmtInt(d.event_count), life: fmtDur(d.lifetime_s) })),
      h("h4", {}, t("ocel.attribute_history")),
      d.attributes.length ? table([
        { key: "name", label: t("ocel.attribute") },
        { key: "value", label: t("ocel.value"), render: (a) => a.value ?? "—" },
        { key: "valid_from", label: t("ocel.valid_from"), render: (a) => (a.valid_from ? fmtTs(a.valid_from, tz, { seconds: true }) : t("ocel.static")) },
      ], d.attributes) : h("p", { class: "muted small" }, "—"),
      h("h4", {}, t("ocel.timeline")),
      h("ol", { class: "timeline" }, d.events.map((e) => h("li", {},
        h("div", {}, h("b", {}, e.event_type), " ", h("span", { class: "small muted" }, fmtTs(e.timestamp, tz, { seconds: true })),
          e.qualifier ? h("span", { class: "small muted" }, ` · ${e.qualifier}`) : null),
        e.other_object_count ? h("div", { class: "small chips" }, t("ocel.shared_with", { n: fmtInt(e.other_object_count) }), " ",
          e.other_objects.map((o) => h("span", {}, link(o), " ", h("span", { class: "muted" }, `(${o.object_type})`)))) : null))),
      h("h4", {}, t("ocel.related_o2o")),
      d.related_via_o2o.length ? table([
        { key: "dir", label: t("ocel.direction"), render: (r) => (r.direction === "out" ? "→" : "←") },
        { key: "q", label: t("ocel.qualifier"), render: (r) => r.qualifier || "—" },
        { key: "o", label: t("ocel.object"), render: link },
        { key: "ty", label: t("ocel.object_type"), render: (r) => r.object_type },
      ], d.related_via_o2o) : h("p", { class: "muted small" }, "—"),
      h("h4", {}, t("ocel.related_events")),
      d.related_via_events.length ? table([
        { key: "o", label: t("ocel.object"), render: link },
        { key: "ty", label: t("ocel.object_type"), render: (r) => r.object_type },
        { key: "n", label: t("ocel.shared_events"), num: true, render: (r) => fmtInt(r.shared_events) },
      ], d.related_via_events) : h("p", { class: "muted small" }, "—"));
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch (err) { showError(box, err); }
}

async function drawRelations() {
  const box = $("#relations-view");
  const ty = $("#rel-type").value;
  if (!ty) return;
  try {
    const r = await api(`/api/ocel/${current.id}/relationship-delays`, { params: { object_type: ty } });
    clear(box, h("p", { class: "small" }, t("ocel.relations_summary", { n: fmtInt(r.objects), type: ty, median: fmtDur(r.median_lifetime_s) })),
      r.groups.length ? r.groups.map((g) => h("div", { style: { marginTop: "10px" } },
        h("h4", {}, t(`ocel.via.${g.via}`, { type: g.related_type })),
        table([
          { key: "related", label: t("ocel.related_objects"), render: (b) => b.related_label },
          { key: "objects", label: t("ocel.objects"), num: true, render: (b) => fmtInt(b.objects) },
          { key: "median", label: t("ocel.median_lifetime"), num: true, render: (b) => fmtDur(b.median_lifetime_s) },
          { key: "p90", label: t("ocel.p90_lifetime"), num: true, render: (b) => fmtDur(b.p90_lifetime_s) },
        ], g.buckets))) : h("p", { class: "muted" }, t("ocel.no_relations")));
  } catch (err) { showError(box, err); }
}

async function drawProjectionPreview() {
  const ty = $("#proj-type").value;
  if (!ty) return;
  try {
    const p = await api(`/api/ocel/${current.id}/projection-preview`, { params: { object_type: ty } });
    clear($("#proj-preview"), h("p", {}, t("ocel.projection_preview", { cases: fmtInt(p.cases), rows: fmtInt(p.rows),
      unique: fmtInt(p.unique_events), dup: fmtInt(p.duplicated_event_rows) })),
    p.duplicated_event_rows ? h("p", { class: "notice warn" }, t("ocel.projection_dup_warning", { dup: fmtInt(p.duplicated_event_rows) })) : null);
  } catch (err) { showError($("#proj-preview"), err); }
}

async function runProjection() {
  const btn = $("#run-projection");
  btn.disabled = true;
  try {
    const res = await api(`/api/ocel/${current.id}/project`, { method: "POST", body: {
      object_type: $("#proj-type").value, dataset_name: $("#proj-name").value || null, acknowledge_flattening: $("#proj-ack").checked } });
    const job = await followJob(res.job.id, $("#proj-status"));
    if (job.status === "succeeded") {
      clear($("#proj-status"), h("p", { class: "notice ok" }, t("ocel.projection_done"), " ",
        h("a", { href: `/?dataset=${job.dataset_id}` }, t("ocel.open_dataset"))));
    } else clear($("#proj-status"), h("p", { class: "notice danger" }, job.error || job.stage));
  } catch (err) { showError($("#proj-status"), err); } finally { btn.disabled = !$("#proj-ack").checked; }
}

function drawOcpnForm() {
  const spec = ocAlgorithms.find((a) => a.id === "oc_petri_net");
  if (!spec) return;
  paramInputs($("#ocpn-form"), spec);
  if (spec.availability.status !== "available") {
    $("#run-ocpn").disabled = true;
    clear($("#ocpn-status"), h("p", { class: "notice warn" }, t(spec.availability.reason_key, spec.availability.reason_params || {})));
  }
}

async function showOcpnRun(run) {
  const view = $("#ocpn-view");
  if (run.status !== "succeeded") {
    clear(view, h("p", { class: "notice warn" }, statusBadge(run.status), " ", errorText(run)), provenance(run, ocAlgorithms));
    return;
  }
  const model = await api(`/api/mining/runs/${run.id}/model`);
  clear(view);
  const host = h("div", {});
  view.append(h("p", { class: "small" }, t("ocel.ocpn_result", { places: fmtInt(run.result.sizes.places),
    transitions: fmtInt(run.result.sizes.transitions), variable: fmtInt(run.result.sizes.variable_arcs),
    s: fmtNum(run.result.discovery_seconds, 2) })), host,
  h("div", { class: "actions", style: { marginTop: "8px" } },
    h("a", { class: "btn btn-sm", href: `/api/mining/runs/${run.id}/export/json`, download: "" }, "JSON"),
    h("button", { type: "button", class: "btn btn-sm", onclick: () => {
      const url = host._modelGraph?.pngDataUrl();
      if (url) { const a = h("a", { href: url, download: `ocpn_${run.id.slice(0, 8)}.png` }); document.body.append(a); a.click(); a.remove(); }
    } }, "PNG")),
  h("details", {}, h("summary", {}, t("disc.provenance")), provenance(run, ocAlgorithms)));
  renderModel(host, model.representations.oc_petri_net, { height: 600 });
}

async function runOcpn() {
  const spec = ocAlgorithms.find((a) => a.id === "oc_petri_net");
  $("#run-ocpn").disabled = true;
  try {
    const res = await api(`/api/ocel/${current.id}/ocpn`, { method: "POST", body: { object_types: selectedTypes, params: readParams($("#ocpn-form"), spec) } });
    if (res.cached) toast(t("disc.cached"));
    const data = res.cached ? await api(`/api/mining/runs/${res.run.id}`) : await followRun(res.run.id, $("#ocpn-status"));
    await showOcpnRun(data.run);
  } catch (err) { showError($("#ocpn-status"), err); } finally { $("#run-ocpn").disabled = false; }
}

function selectTab(name) {
  tab = name;
  $$("#ocel-tabs .tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  setUrl();
  if (name === "ocdfg") drawOcdfg();
  if (name === "objects") drawObjects();
  if (name === "relations") drawRelations();
  if (name === "projection") drawProjectionPreview();
}

async function refresh() {
  if (!current) return;
  await drawStats().catch((err) => showError($("#kpis"), err));
  selectTab(tab);
}

async function render() {
  try {
    if (!ocAlgorithms.length) ocAlgorithms = (await api("/api/mining/algorithms")).object_centric || [];
    await loadLogs();
  } catch (err) { showError($("#log-info"), err); return; }
  if (!current) return;
  selectedTypes = (params().get("types") || "").split(",").filter(Boolean);
  tab = params().get("tab") || "ocdfg";
  $("#ocel-body").classList.remove("hidden");
  drawInfo();
  drawTypeFilter();
  drawOcpnForm();
  setUrl();
  refresh();
}

async function afterImport(log) {
  toast(t("ocel.imported", { name: log.name, events: fmtInt(log.counts.events), objects: fmtInt(log.counts.objects) }));
  selectedTypes = [];
  history.replaceState(null, "", `${location.pathname}?log=${log.id}`);
  render();
}

$("#log-select").addEventListener("change", (e) => { history.replaceState(null, "", `${location.pathname}?log=${e.target.value}`); selectedTypes = []; render(); });
$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#ocel-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  clear($("#upload-status"), h("p", { class: "small muted" }, t("common.loading")));
  try {
    const log = await api("/api/ocel/upload", { method: "POST", form });
    clear($("#upload-status"));
    afterImport(log);
  } catch (err) {
    clear($("#upload-status"), h("p", { class: "notice danger" }, err.message),
      Array.isArray(err.details) ? findingsBlock(err.details) : null);
    const d = $("#upload-status details");
    if (d) d.open = true;
  }
});
$("#load-demo").addEventListener("click", async () => {
  $("#load-demo").disabled = true;
  try { afterImport(await api("/api/ocel/demo", { method: "POST" })); } catch (err) { showError($("#upload-status"), err); } finally { $("#load-demo").disabled = false; }
});
$("#delete-log").addEventListener("click", async () => {
  if (!current || !confirm(t("ocel.confirm_delete", { name: current.name }))) return;
  try {
    await api(`/api/ocel/${current.id}`, { method: "DELETE" });
    history.replaceState(null, "", location.pathname);
    render();
  } catch (err) { toast(err.message, "error"); }
});
$$("#ocel-tabs .tab").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
$("#obj-type").addEventListener("change", () => { objPage = 1; drawObjects(); });
let searchTimer;
$("#obj-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { objPage = 1; drawObjects(); }, 250); });
$("#rel-type").addEventListener("change", drawRelations);
$("#proj-type").addEventListener("change", drawProjectionPreview);
$("#proj-ack").addEventListener("change", (e) => { $("#run-projection").disabled = !e.target.checked; });
$("#run-projection").addEventListener("click", runProjection);
$("#run-ocpn").addEventListener("click", runOcpn);
bootPage(render, { filters: false });
