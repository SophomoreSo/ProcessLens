import { algorithmInfo, algorithmOptions, loadRegistry, paramInputs, readParams } from "./algoforms.js";
import {
  $, $$, api, badge, bootPage, IS_ADMIN, clear, currentDatasetId, filterParams, fmtInt, fmtNum, fmtTs, getFilterOptions, h,
  linkWith, loading, noDatasetState, pager, reveal, showError, t, table, toast,
} from "./core.js";
import { downloadPng, renderModel, sizeText } from "./models.js";
import { algoName, errorText, followRun, methodCard, provenance, statusBadge } from "./runs.js";

let registry = [];
let current = null; // {run, artifacts, reps}
let references = [];
const PETRI_METHODS = ["token_replay", "alignments", "soundness"];

function spec(id) { return registry.find((a) => a.id === id); }

function drawAlgorithm() {
  if (!$("#algorithm")) return; // guest layout: no discovery form
  const s = spec($("#algorithm").value);
  if (!s) return;
  clear($("#algo-info"), algorithmInfo(s));
  paramInputs($("#param-form"), s, getFilterOptions()?.activities || []);
  $("#run-discovery").disabled = s.availability.status !== "available";
}

async function runDiscovery() {
  const ds = currentDatasetId();
  const s = spec($("#algorithm").value);
  const status = $("#run-status");
  $("#run-discovery").disabled = true;
  try {
    const res = await api(`/api/datasets/${ds}/mining/discover`, {
      method: "POST", params: filterParams(),
      body: { algorithm_id: s.id, params: readParams($("#param-form"), s), timeout_s: Number($("#timeout").value) || null,
        force: $("#force").checked },
    });
    if (res.cached) toast(t("disc.cached"));
    const data = res.cached ? await api(`/api/mining/runs/${res.run.id}`) : await followRun(res.run.id, status);
    await showRun(data);
    loadHistory();
  } catch (err) {
    showError(status, err);
  } finally {
    $("#run-discovery").disabled = false;
  }
}

async function showRun(data) {
  const run = data.run;
  const card = $("#result-card");
  card.classList.remove("hidden");
  if (run.kind === "conformance") return showConformance(data);
  if (run.status !== "succeeded") {
    clear(card, h("div", { class: "card-head" }, h("h2", {}, algoName(registry, run.algorithm_id)), statusBadge(run.status)),
      h("p", { class: "notice warn" }, errorText(run) || t(`run.status.${run.status}`)), provenance(run, registry));
    return;
  }
  const model = await api(`/api/mining/runs/${run.id}/model`);
  current = { run, artifacts: data.artifacts, reps: model.representations };
  const native = run.result.native_type;
  const order = [native, ...Object.keys(current.reps).filter((k) => k !== native)];
  const viewHost = h("div", {});
  const tabs = h("div", { class: "tabs", role: "tablist" }, order.map((rt) => h("button", {
    type: "button", role: "tab", class: "tab", dataset: { rep: rt },
    onclick: () => selectRep(rt),
  }, `${t(`model.type.${rt}`)}${rt === native ? ` (${t("disc.native")})` : ` (${t("disc.derived")})`}`)));
  const sizeLine = h("p", { class: "small muted" });
  const exports = h("div", { class: "actions" });
  const selectRep = (rt) => {
    $$(".tab", tabs).forEach((b) => b.setAttribute("aria-selected", String(b.dataset.rep === rt)));
    const graph = renderModel(viewHost, current.reps[rt]);
    sizeLine.textContent = sizeText(rt, run.result.sizes[rt]);
    const derived = current.reps[rt].derived_from;
    if (derived) sizeLine.textContent += ` · ${t("disc.derived_from", { type: t(`model.type.${derived}`) })}`;
    clear(exports, h("span", { class: "muted small" }, t("disc.export")),
      ["ptml", "pnml", "bpmn", "json"].filter((f) => data.artifacts.includes(f === "json" ? "model.json" : `model.${f}`))
        .map((f) => h("a", { class: "btn btn-sm", href: `/api/mining/runs/${run.id}/export/${f}`, download: "" }, f.toUpperCase())),
      h("a", { class: "btn btn-sm", href: `/api/mining/runs/${run.id}/export/representation`, download: "" }, t("disc.export_representation")),
      graph ? h("button", { type: "button", class: "btn btn-sm", onclick: () => downloadPng(graph, `processlens_${run.id.slice(0, 8)}_${rt}.png`) }, "PNG") : null);
  };
  const warnings = (run.result.warnings || []).map((w) => h("p", { class: "notice warn small" }, t(w.key, w.params || {})));
  clear(card,
    h("div", { class: "card-head" }, h("h2", {}, algoName(registry, run.algorithm_id)), statusBadge(run.status)),
    h("div", {}, tabs, viewHost, sizeLine, exports),
    h("div", { class: "grid-2", style: { marginTop: "12px" } },
      h("div", {}, h("h3", {}, t("disc.provenance")), provenance(run, registry),
        h("p", { class: "small muted" }, t("disc.discovery_seconds", { s: fmtNum(run.result.discovery_seconds, 3) })),
        warnings),
      !IS_ADMIN ? null : h("div", {},
        h("h3", {}, t("ref.approve_title")),
        h("form", { class: "form-grid", onsubmit: async (e) => {
          e.preventDefault();
          const f = e.target;
          try {
            await api(`/api/datasets/${currentDatasetId()}/reference-models`, { method: "POST",
              body: { run_id: run.id, name: f.elements.name.value, note: f.elements.note.value || null } });
            toast(t("ref.approved"));
            loadReferences();
          } catch (err) { toast(err.message, "error"); }
        } }, h("label", {}, t("common.name"), h("input", { name: "name", required: true, maxlength: "200",
          value: `${algoName(registry, run.algorithm_id)} · v${run.dataset_version}` })),
        h("label", {}, t("ref.note"), h("input", { name: "note", maxlength: "2000" })),
        h("div", { class: "actions", style: { alignSelf: "end" } }, h("button", { type: "submit", class: "btn btn-sm" }, t("ref.approve")))))));
  selectRep(native);
  reveal(card);
  drawConformanceForm();
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

// --------------------------------------------------------------------------- conformance
function methodsFor(modelType, hasPetri) {
  if (modelType === "declare") return ["declare_conformance"];
  if (modelType === "log_skeleton") return ["log_skeleton_conformance"];
  if (modelType === "temporal_profile") return ["temporal_profile_conformance"];
  return hasPetri ? PETRI_METHODS : [];
}

function drawConformanceForm() {
  const card = $("#conformance-card");
  card.classList.remove("hidden");
  if (!IS_ADMIN) {
    clear(card, h("h2", {}, t("conf2.title")), h("p", { class: "small muted" }, t("guest.conformance_note")), h("div", { id: "conf-result" }));
    return;
  }
  const sourceSel = h("select", { name: "source" },
    current ? h("option", { value: `run:${current.run.id}` }, t("conf2.source_discovered", { name: algoName(registry, current.run.algorithm_id) })) : null,
    references.map((r) => h("option", { value: `ref:${r.id}` }, t("conf2.source_reference", { name: r.name }))));
  const methodsBox = h("div", { class: "actions" });
  const updateMethods = () => {
    const [kind, id] = sourceSel.value.split(":");
    let methods = [];
    if (kind === "run" && current) methods = methodsFor(current.run.result.native_type, current.artifacts.includes("model.pnml"));
    if (kind === "ref") { const r = references.find((x) => String(x.id) === id); if (r) methods = methodsFor(r.model_type, r.has_petri_net); }
    clear(methodsBox, methods.length ? methods.map((m) => h("label", { class: "inline" },
      h("input", { type: "checkbox", name: "method", value: m, checked: true }), t(`method.${m}`)))
      : h("span", { class: "muted small" }, t("run.note.no_evaluation_method")));
  };
  sourceSel.addEventListener("change", updateMethods);
  const form = h("form", { onsubmit: runConformance },
    h("div", { class: "form-grid" },
      h("label", {}, t("conf2.model"), sourceSel),
      h("label", {}, t("conf2.max_variants"), h("input", { type: "number", name: "max_variants", min: "1", value: "2000" })),
      h("label", {}, t("conf2.zeta"), h("input", { type: "number", name: "zeta", min: "0.1", step: "0.1", value: "2" })),
      h("label", { class: "inline", style: { alignSelf: "end" } }, h("input", { type: "checkbox", name: "precision", checked: true }), t("conf2.precision"))),
    h("fieldset", { style: { marginTop: "10px" } }, h("legend", {}, t("conf2.methods")), methodsBox),
    h("p", { class: "small muted" }, t("conf2.population_note")),
    h("div", { class: "actions" }, h("button", { type: "submit", class: "btn btn-primary" }, t("conf2.run"))));
  clear(card, h("h2", {}, t("conf2.title")), h("p", { class: "small muted" }, t("conf2.intro")), form, h("div", { id: "conf-status" }), h("div", { id: "conf-result" }));
  updateMethods();
}

async function runConformance(e) {
  e.preventDefault();
  const f = e.target;
  const [kind, id] = f.elements.source.value.split(":");
  const methods = $$('input[name="method"]:checked', f).map((x) => x.value);
  if (!methods.length) return toast(t("conf2.choose_method"), "error");
  const body = { model: kind === "run" ? { run_id: id } : { reference_model_id: Number(id) }, methods,
    limits: { max_variants: Number(f.elements.max_variants.value), zeta: Number(f.elements.zeta.value), precision: f.elements.precision.checked },
    timeout_s: Number($("#timeout").value) || null };
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/mining/conformance`, { method: "POST", params: filterParams(), body });
    const data = res.cached ? await api(`/api/mining/runs/${res.run.id}`) : await followRun(res.run.id, $("#conf-status"));
    showConformance(data);
    loadHistory();
  } catch (err) { showError($("#conf-status"), err); }
}

function showConformance(data) {
  const run = data.run;
  if (!$("#conf-result")) { current = null; drawConformanceForm(); }
  const out = $("#conf-result");
  if (run.status !== "succeeded") {
    clear(out, h("p", { class: "notice warn" }, statusBadge(run.status), " ", errorText(run)), provenance(run, registry));
    return;
  }
  const src = run.result.model_source;
  const srcText = src.type === "reference"
    ? t("conf2.against_reference", { name: src.name, date: fmtTs(src.approved_at + "Z", "UTC", { dateOnly: true }) })
    : t("conf2.against_discovered", { name: algoName(registry, src.algorithm_id) });
  const casesBox = h("div", {});
  clear(out,
    h("p", { class: `notice ${src.type === "reference" ? "ok" : "info"}` }, h("b", {}, src.type === "reference" ? t("conf2.reference_badge") : t("conf2.discovered_badge")), " ", srcText),
    h("p", { class: "small muted" }, t("conf2.evaluated", { n: fmtInt(run.result.evaluated_cases) })),
    h("div", { class: "grid-3" }, Object.entries(run.result.methods).map(([m, r]) => methodCard(m, r, { onCases: (method) => showCases(run.id, method, casesBox, 1) }))),
    casesBox,
    h("details", {}, h("summary", {}, t("disc.provenance")), provenance(run, registry)));
  reveal(out.querySelector(".grid-3"));
}

async function showCases(runId, method, box, page, onlyDev = true) {
  loading(box);
  let res;
  try { res = await api(`/api/mining/runs/${runId}/cases`, { params: { method, page, page_size: 20, only_deviating: onlyDev } }); } catch (err) { return showError(box, err); }
  const toggle = h("label", { class: "inline" }, h("input", { type: "checkbox", checked: onlyDev, onchange: (e) => showCases(runId, method, box, 1, e.target.checked) }), t("conf2.only_deviating"));
  const devText = (c) => {
    if (c.deviations && c.deviations.length) {
      return h("ul", { class: "small" }, c.deviations.slice(0, 8).map((d) => h("li", {}, typeof d === "string" ? d
        : d.type ? `${t(`conf2.move.${d.type}`)}: ${d.activity}` : `${d.source} → ${d.target}: ${fmtNum(d.seconds / 3600, 1)} h (z = ${fmtNum(d.z, 2)})`)));
    }
    if (c.missing !== undefined) return t("conf2.tokens", { missing: c.missing, remaining: c.remaining });
    return "—";
  };
  clear(box, h("div", { class: "card-head" }, h("h3", {}, `${t("conf2.cases")}: ${t(`method.${method}`)}`), toggle),
    table([
      { key: "case_id", label: t("common.case"), render: (c) => h("a", { class: "mono", href: linkWith("/cases", { extra: { case: c.case_id } }) }, c.case_id) },
      { key: "fit", label: t("conf2.fit"), render: (c) => (c.is_fit ? badge(t("result.compliant"), "ok") : badge(t("result.violating"), "danger")) },
      { key: "fitness", label: t("metric.fitness"), num: true, render: (c) => (c.fitness !== undefined ? fmtNum(c.fitness, 3) : "—") },
      { key: "dev", label: t("conf2.deviations"), render: devText },
    ], res.items),
    pager({ page: res.page, pageSize: res.page_size, total: res.total }, (p) => showCases(runId, method, box, p, onlyDev)));
}

// --------------------------------------------------------------------------- history & references
async function loadHistory() {
  const box = $("#history");
  const ds = currentDatasetId();
  try {
    const res = await api("/api/mining/runs", { params: { dataset_id: ds, page_size: 15 } });
    if (!res.total) return clear(box, h("p", { class: "muted small" }, t("disc.no_runs")));
    clear(box, table([
      { key: "kind", label: t("run.prov.kind"), render: (r) => t(`run.kind.${r.kind}`) },
      { key: "algorithm", label: t("run.prov.algorithm"), render: (r) => (r.algorithm_id ? algoName(registry, r.algorithm_id)
        : r.kind === "conformance" ? (r.request.methods || []).map((m) => t(`method.${m}`)).join(", ") : "—") },
      { key: "status", label: t("common.status"), render: (r) => statusBadge(r.status) },
      { key: "cases", label: t("common.cases"), num: true, render: (r) => fmtInt(r.filter_snapshot?.case_count) },
      { key: "created", label: t("run.prov.created"), render: (r) => fmtTs(`${r.created_at}Z`, "UTC") },
    ], res.items, { onRowClick: async (r) => {
      if (r.kind === "comparison") { location.href = linkWith("/compare", { extra: { run: r.id } }); return; }
      if (r.kind === "model_import") { loadReferences(); return; }
      showRun(await api(`/api/mining/runs/${r.id}`));
    } }));
  } catch (err) { showError(box, err); }
}

async function loadReferences() {
  const box = $("#references");
  try {
    references = (await api(`/api/datasets/${currentDatasetId()}/reference-models`)).items;
  } catch (err) { return showError(box, err); }
  clear(box, references.length ? table([
    { key: "name", label: t("common.name"), render: (r) => h("div", {}, h("b", {}, r.name), r.note ? h("div", { class: "small muted" }, r.note) : null) },
    { key: "type", label: t("ref.model_type"), render: (r) => t(`model.type.${r.model_type}`) },
    { key: "source", label: t("ref.source"), render: (r) => (r.source.type === "upload" ? t("ref.source_upload", { file: r.source.filename })
      : t("ref.source_run", { algorithm: algoName(registry, r.source.algorithm_id) })) },
    { key: "approved", label: t("ref.approved_at"), render: (r) => fmtTs(`${r.approved_at}Z`, "UTC") },
    { key: "actions", label: "", render: (r) => h("div", { class: "actions" },
      r.has_petri_net ? h("a", { class: "btn btn-sm", href: `/api/datasets/${currentDatasetId()}/reference-models/${r.id}/export.pnml`, download: "" }, "PNML") : null,
      IS_ADMIN ? h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => {
        if (!confirm(t("ref.delete_confirm", { name: r.name }))) return;
        await api(`/api/datasets/${currentDatasetId()}/reference-models/${r.id}`, { method: "DELETE" });
        loadReferences();
      } }, t("common.delete")) : null) },
  ], references) : h("p", { class: "muted small" }, t("ref.none")));
  if (!$("#conformance-card").classList.contains("hidden")) drawConformanceForm();
}

$("#ref-upload")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/reference-models/upload`, { method: "POST", form });
    const data = await followRun(res.run.id, $("#ref-status"));
    if (data.run.status === "succeeded") { toast(t("ref.imported")); e.target.reset(); }
    else clear($("#ref-status"), h("p", { class: "notice error" }, errorText(data.run)));
    loadReferences();
  } catch (err) { showError($("#ref-status"), err); }
});

async function render() {
  const ds = currentDatasetId();
  if (!ds) {
    noDatasetState($("#history"));
    if ($("#run-discovery")) $("#run-discovery").disabled = true;
    return;
  }
  if (!registry.length) {
    registry = await loadRegistry(api);
    if ($("#algorithm")) algorithmOptions($("#algorithm"), registry, "inductive_infrequent");
  }
  drawAlgorithm();
  loadHistory();
  await loadReferences();
  const pre = new URLSearchParams(location.search).get("run");
  if (pre) showRun(await api(`/api/mining/runs/${pre}`));
}

$("#algorithm")?.addEventListener("change", drawAlgorithm);
$("#run-discovery")?.addEventListener("click", runDiscovery);
bootPage(render);
