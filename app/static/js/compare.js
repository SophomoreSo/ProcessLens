import { loadRegistry, paramInputs, readParams } from "./algoforms.js";
import {
  $, $$, api, badge, bootPage, clear, currentDatasetId, filterParams, fmtInt, fmtNum, fmtTs, getFilterOptions, h,
  noDatasetState, reveal, showError, t, table, toast,
} from "./core.js";
import { renderModel, sizeText } from "./models.js";
import { algoName, errorText, followRun, metricValue, methodStatusBadge, provenance, statusBadge } from "./runs.js";

let registry = [];
let lastRun = null;
const DEFAULT_SELECTION = ["inductive_infrequent", "heuristics", "alpha", "log_skeleton"];

function drawChoices() {
  const box = $("#algo-choices");
  const activities = getFilterOptions()?.activities || [];
  clear(box, registry.map((a) => {
    const available = a.availability.status === "available";
    const form = h("form", { class: "form-grid" });
    paramInputs(form, a, activities);
    return h("details", { class: "algo-choice" },
      h("summary", {}, h("label", { class: "inline", onclick: (e) => e.stopPropagation() },
        h("input", { type: "checkbox", name: "algo", value: a.id, disabled: !available, checked: available && DEFAULT_SELECTION.includes(a.id) }),
        t(a.name_key)), available ? null : badge(t("algo.unavailable_short"), "warn")),
      available ? form : h("p", { class: "small notice warn" }, t(a.availability.reason_key, a.availability.reason_params || {})));
  }));
}

async function runCompare() {
  const chosen = $$('input[name="algo"]:checked');
  if (!chosen.length) return toast(t("cmp2.choose_algorithms"), "error");
  const algorithms = chosen.map((cb) => {
    const a = registry.find((x) => x.id === cb.value);
    return { algorithm_id: a.id, params: readParams(cb.closest("details").querySelector("form"), a) };
  });
  const body = {
    algorithms,
    split: { enabled: $("#split-enabled").checked, train_ratio: Number($("#train-ratio").value), seed: Number($("#seed").value) },
    evaluation: { token_replay: $("#ev-tbr").checked, alignments: $("#ev-align").checked, soundness: $("#ev-sound").checked,
      declarative: $("#ev-decl").checked, precision: $("#ev-prec").checked, max_variants: Number($("#max-variants").value) },
    timeout_s: Number($("#cmp-timeout").value) || null,
  };
  $("#run-compare").disabled = true;
  try {
    const res = await api(`/api/datasets/${currentDatasetId()}/mining/compare`, { method: "POST", params: filterParams(), body });
    if (res.cached) toast(t("disc.cached"));
    const data = res.cached ? await api(`/api/mining/runs/${res.run.id}`) : await followRun(res.run.id, $("#cmp-status"));
    showComparison(data.run);
    loadHistory();
  } catch (err) { showError($("#cmp-status"), err); } finally { $("#run-compare").disabled = false; }
}

function metricCell(row, method, key) {
  const m = (row.evaluation || {})[method];
  if (!m) return h("span", { class: "muted small" }, t("mstatus.not_requested"));
  if (m.status !== "computed") return h("span", { title: m.reason_key ? t(m.reason_key, m.reason_params || {}) : "" }, methodStatusBadge(m.status));
  if (key === "precision") {
    const p = m.precision || {};
    return p.status === "computed" ? metricValue("precision", p.value) : methodStatusBadge(p.status || "not_evaluable");
  }
  if (key === "sound") return m.sound ? badge(t("common.yes"), "ok") : badge(t("common.no"), "danger");
  const v = (m.metrics || {})[key];
  return v === undefined ? "—" : metricValue(key, v);
}

function declarativeCell(row) {
  for (const method of ["declare_conformance", "log_skeleton_conformance", "temporal_profile_conformance"]) {
    const m = (row.evaluation || {})[method];
    if (!m) continue;
    if (m.status !== "computed") return methodStatusBadge(m.status);
    const share = m.metrics.share_fitting ?? m.metrics.share_without_deviation;
    return h("span", { title: t(`method.${method}`) }, metricValue("share", share));
  }
  return h("span", { class: "muted small" }, "—");
}

function showComparison(run) {
  lastRun = run;
  const card = $("#cmp-result");
  card.classList.remove("hidden");
  if (run.status !== "succeeded") {
    clear(card, h("h2", {}, t("cmp2.results")), h("p", { class: "notice warn" }, statusBadge(run.status), " ", errorText(run)), provenance(run, registry));
    return;
  }
  const res = run.result;
  const sampleNote = res.sample === "held_out"
    ? t("cmp2.held_out_note", { train: fmtInt(res.training_cases), test: fmtInt(res.evaluated_cases) })
    : t("cmp2.in_sample_note", { n: fmtInt(res.evaluated_cases) });
  clear(card,
    h("div", { class: "card-head" }, h("h2", {}, t("cmp2.results")),
      h("div", { class: "actions" },
        h("a", { class: "btn btn-sm", href: `/api/mining/runs/${run.id}/export/comparison.csv`, download: "" }, "CSV"),
        h("a", { class: "btn btn-sm", href: `/api/mining/runs/${run.id}/export/comparison.json`, download: "" }, "JSON"))),
    h("p", { class: `notice ${res.sample === "held_out" ? "ok" : "warn"} small` }, sampleNote),
    h("p", { class: "small muted" }, t("cmp2.no_overall_score")),
    table([
      { key: "algorithm", label: t("run.prov.algorithm"), render: (r) => h("div", {}, h("b", {}, algoName(registry, r.algorithm_id)),
        Object.keys(r.params || {}).length ? h("div", { class: "small muted mono" }, JSON.stringify(r.params)) : null) },
      { key: "status", label: t("common.status"), render: (r) => h("div", {}, statusBadge(r.status), r.error_key ? h("div", { class: "small" }, t(r.error_key, r.error_params || {})) : null) },
      { key: "type", label: t("cmp2.representation"), render: (r) => (r.native_type ? t(`model.type.${r.native_type}`) : "—") },
      { key: "runtime", label: t("cmp2.runtime"), num: true, render: (r) => (r.discovery_seconds !== undefined ? `${fmtNum(r.discovery_seconds, 3)} s` : "—") },
      { key: "size", label: t("cmp2.size"), render: (r) => h("span", { class: "small" }, r.native_type ? sizeText(r.native_type, r.sizes?.[r.native_type]) : "—") },
      { key: "tbr_f", label: t("cmp2.tbr_fitness"), num: true, render: (r) => metricCell(r, "token_replay", "log_fitness") },
      { key: "tbr_p", label: t("cmp2.tbr_precision"), num: true, render: (r) => metricCell(r, "token_replay", "precision") },
      { key: "al_f", label: t("cmp2.align_fitness"), num: true, render: (r) => metricCell(r, "alignments", "log_fitness") },
      { key: "al_p", label: t("cmp2.align_precision"), num: true, render: (r) => metricCell(r, "alignments", "precision") },
      { key: "sound", label: t("method.soundness"), render: (r) => metricCell(r, "soundness", "sound") },
      { key: "decl", label: t("cmp2.declarative_fit"), num: true, render: declarativeCell },
      { key: "warn", label: t("cmp2.warnings"), render: (r) => [
        ...(r.warnings || []).map((w) => h("div", { class: "small" }, t(w.key, w.params || {}))),
        r.evaluation_note ? h("div", { class: "small muted" }, t(r.evaluation_note)) : null] },
    ], res.rows),
    h("details", {}, h("summary", {}, t("disc.provenance")), provenance(run, registry)));
  reveal(card.querySelector("tbody"), { stagger: 40 });
  drawSideBySide(run);
}

function drawSideBySide(run) {
  const ok = run.result.rows.filter((r) => r.status === "succeeded");
  const box = $("#side-by-side");
  box.classList.toggle("hidden", !ok.length);
  for (const [sel, view, idx] of [["#left-model", "#left-view", 0], ["#right-model", "#right-view", Math.min(1, ok.length - 1)]]) {
    const select = $(sel);
    clear(select, ok.flatMap((r) => r.representations.map((rep) => h("option", { value: `${r.index}:${rep}` },
      `${algoName(registry, r.algorithm_id)} — ${t(`model.type.${rep}`)}`))));
    const first = ok[idx];
    if (first) select.value = `${first.index}:${first.native_type}`;
    select.onchange = () => showSide(run.id, select.value, $(view));
    if (first) showSide(run.id, select.value, $(view));
  }
}

async function showSide(runId, value, host) {
  const [index, rep] = value.split(":");
  try {
    const model = await api(`/api/mining/runs/${runId}/model`, { params: { index } });
    renderModel(host, model.representations[rep], { height: 420 });
  } catch (err) { showError(host, err); }
}

async function loadHistory() {
  const box = $("#cmp-history");
  try {
    const res = await api("/api/mining/runs", { params: { dataset_id: currentDatasetId(), kind: "comparison", page_size: 10 } });
    if (!res.total) return clear(box, h("p", { class: "muted small" }, t("cmp2.no_history")));
    clear(box, table([
      { key: "created", label: t("run.prov.created"), render: (r) => fmtTs(`${r.created_at}Z`, "UTC") },
      { key: "algos", label: t("cmp2.algorithms"), render: (r) => (r.request.algorithms || []).map((a) => algoName(registry, a.algorithm_id)).join(", ") },
      { key: "split", label: t("run.prov.split"), render: (r) => (r.split ? `${fmtInt(r.split.train_cases)} / ${fmtInt(r.split.test_cases)}` : t("cmp2.no_split")) },
      { key: "status", label: t("common.status"), render: (r) => statusBadge(r.status) },
    ], res.items, { onRowClick: async (r) => showComparison((await api(`/api/mining/runs/${r.id}`)).run) }));
  } catch (err) { showError(box, err); }
}

async function render() {
  if (!currentDatasetId()) {
    noDatasetState($("#algo-choices") || $("#cmp-history"));
    if ($("#run-compare")) $("#run-compare").disabled = true;
    return;
  }
  if (!registry.length) registry = await loadRegistry(api);
  if ($("#algo-choices")) drawChoices(); // admin layout only
  loadHistory();
  const pre = new URLSearchParams(location.search).get("run");
  if (pre && !lastRun) showComparison((await api(`/api/mining/runs/${pre}`)).run);
}

$("#run-compare")?.addEventListener("click", runCompare);
bootPage(render);
