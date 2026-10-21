// Shared helpers for mining runs: following progress, cancellation, provenance and result cards.
import { api, badge, clear, fmtInt, fmtNum, fmtPct, fmtTs, h, t, toast } from "./core.js";

const STATUS_CLASS = { succeeded: "ok", failed: "danger", cancelled: "warn", timed_out: "warn", interrupted: "warn",
  running: "info", queued: "info", computed: "ok", unsupported: "", not_evaluable: "warn", not_requested: "" };

export function statusBadge(status) {
  return badge(t(`run.status.${status}`), STATUS_CLASS[status] ?? "");
}

export function methodStatusBadge(status) {
  return badge(t(`mstatus.${status}`), STATUS_CLASS[status] ?? "");
}

export function errorText(run) {
  return run.error_key ? t(run.error_key, run.error_params || {}) : "";
}

export function algoName(registry, id) {
  const a = registry?.find((x) => x.id === id);
  return a ? t(a.name_key) : id || "—";
}

/** Poll a run until it reaches a terminal state, showing honest stage text and progress (if measurable). */
export async function followRun(runId, el) {
  const stage = h("span", {}, t("run.stage.queued"));
  const bar = h("i", { style: { width: "0%" } });
  const progress = h("div", { class: "progress hidden", style: { marginTop: "8px" }, role: "progressbar" }, bar);
  const cancelBtn = h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => {
    cancelBtn.disabled = true;
    try { await api(`/api/mining/runs/${runId}/cancel`, { method: "POST" }); } catch (err) { toast(err.message, "error"); }
  } }, t("run.cancel"));
  const elapsed = h("span", { class: "muted small" });
  const started = Date.now();
  clear(el, h("div", { class: "notice info" }, h("div", { class: "actions" }, h("span", { class: "spinner" }), stage,
    h("span", { class: "grow" }), elapsed, cancelBtn), progress));
  for (;;) {
    let data;
    try {
      data = await api(`/api/mining/runs/${runId}`);
    } catch (err) {
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }
    const run = data.run;
    stage.textContent = t(run.stage_key || "run.stage.queued", run.stage_params || {});
    elapsed.textContent = t("run.elapsed", { s: Math.round((Date.now() - started) / 1000) });
    if (typeof run.progress === "number" && run.status === "running") {
      progress.classList.remove("hidden");
      bar.style.width = `${Math.round(run.progress * 100)}%`;
    }
    if (!["queued", "running"].includes(run.status)) { clear(el); return data; }
    await new Promise((r) => setTimeout(r, 500));
  }
}

export function provenance(run, registry) {
  const rows = [
    [t("run.prov.run"), h("code", {}, run.id.slice(0, 12))],
    [t("run.prov.kind"), t(`run.kind.${run.kind}`)],
    run.algorithm_id ? [t("run.prov.algorithm"), algoName(registry, run.algorithm_id)] : null,
    [t("run.prov.implementation"), `${run.implementation?.provider || "—"} ${run.implementation?.provider_version || ""} · ${t("run.prov.adapter")} ${run.implementation?.adapter_version || "—"}`],
    run.params && Object.keys(run.params).length ? [t("run.prov.params"), h("code", {}, JSON.stringify(run.params))] : null,
    run.dataset_version ? [t("run.prov.dataset_version"), `v${run.dataset_version}`] : null,
    run.filter_snapshot ? [t("run.prov.population"), t("run.prov.population_value", { n: fmtInt(run.filter_snapshot.case_count),
      filters: Object.keys(run.filter_snapshot.filters || {}).length ? JSON.stringify(run.filter_snapshot.filters) : t("run.prov.no_filters") })] : null,
    run.split ? [t("run.prov.split"), t("run.prov.split_value", { train: fmtInt(run.split.train_cases), test: fmtInt(run.split.test_cases),
      ratio: fmtPct(run.split.train_ratio, 0), seed: run.split.seed })] : null,
    [t("run.prov.created"), fmtTs(`${run.created_at}Z`, "UTC", { seconds: true })],
    run.runtime_s !== null && run.runtime_s !== undefined ? [t("run.prov.runtime"), `${fmtNum(run.runtime_s, 2)} s`] : null,
    [t("common.status"), statusBadge(run.status)],
  ].filter(Boolean);
  return h("dl", { class: "prov" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

const METRIC_LABELS = {
  log_fitness: "metric.log_fitness", average_trace_fitness: "metric.average_trace_fitness",
  percentage_of_fitting_traces: "metric.percentage_fitting", variants_aligned: "metric.variants_aligned",
  cases: "metric.cases", fitting_cases: "metric.fitting_cases", share_fitting: "metric.share_fitting",
  average_fitness: "metric.average_fitness", cases_without_deviation: "metric.cases_without_deviation",
  share_without_deviation: "metric.share_without_deviation",
};

export function metricValue(key, value) {
  if (value === null || value === undefined) return "—";
  if (key === "percentage_of_fitting_traces") return fmtPct(value / 100);
  if (key.startsWith("share")) return fmtPct(value);
  if (Number.isInteger(value)) return fmtInt(value);
  return fmtNum(value, 3);
}

/** One conformance method result: status, metrics, precision, explicit reasons. */
export function methodCard(method, res, { onCases } = {}) {
  const parts = [h("div", { class: "card-head" }, h("h3", {}, t(`method.${method}`)), methodStatusBadge(res.status))];
  if (res.status !== "computed") {
    parts.push(h("p", { class: "small" }, res.reason_key ? t(res.reason_key, res.reason_params || {}) : t(`mstatus.${res.status}_hint`)));
  } else {
    const dl = [];
    for (const [k, v] of Object.entries(res.metrics || {})) dl.push([t(METRIC_LABELS[k] || k), metricValue(k, v)]);
    if (res.precision) {
      dl.push([t("metric.precision"), res.precision.status === "computed" ? metricValue("precision", res.precision.value)
        : h("span", {}, methodStatusBadge(res.precision.status), " ", res.precision.reason_key ? t(res.precision.reason_key) : "")]);
    }
    if (method === "soundness") dl.push([t("metric.sound"), res.sound ? badge(t("common.yes"), "ok") : badge(t("common.no"), "danger")]);
    if (res.zeta !== undefined) dl.push(["ζ", fmtNum(res.zeta, 2)]);
    if (res.case_count !== undefined) dl.push([t("metric.deviating_cases"), `${fmtInt(res.deviating_cases)} / ${fmtInt(res.case_count)}`]);
    parts.push(h("dl", { class: "prov" }, dl.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
    if (onCases && res.case_count !== undefined) {
      parts.push(h("button", { type: "button", class: "btn btn-sm", onclick: () => onCases(method) }, t("conf2.show_cases")));
    }
  }
  if (res.seconds !== undefined) parts.push(h("p", { class: "small muted" }, t("run.seconds", { s: fmtNum(res.seconds, 2) })));
  return h("section", { class: "card method-card" }, parts);
}
