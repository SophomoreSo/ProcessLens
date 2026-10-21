import {
  $, api, badge, bootPage, clear, currentDatasetId, fmtInt, fmtTs, getFilterOptions, h, linkWith, loading,
  noDatasetState, pager, refreshAlertBadge, reveal, SEVERITY_LABEL, showError, t, table, toast,
} from "./core.js";

const TYPES = {
  duration_threshold: t("alert.type.duration_threshold"),
  rework: t("alert.type.rework"),
  conformance_violation: t("alert.type.conformance_violation"),
};
const METRICS = {
  observed_duration: t("alert.metric.observed_duration"),
  completed_throughput: t("alert.metric.completed_throughput"),
  open_case_age: t("alert.metric.open_case_age"),
};
const STATUS = { open: t("alert.status.open"), acknowledged: t("alert.status.acknowledged"), resolved: t("alert.status.resolved") };
let page = 1;

function describe(rule, crules) {
  const p = rule.params || {};
  if (rule.alert_type === "duration_threshold") return t("al.desc.duration", { metric: METRICS[p.metric] || p.metric, hours: p.threshold_hours });
  if (rule.alert_type === "rework") return p.activity ? t("al.desc.rework_activity", { activity: p.activity }) : t("al.desc.rework_any");
  const cr = crules.find((r) => r.id === rule.conformance_rule_id);
  return t("al.desc.conformance", { name: cr ? cr.name : t("chip.rule_id", { id: rule.conformance_rule_id }) });
}

function openEditor(rule = null) {
  const card = $("#alert-rule-editor");
  card.classList.remove("hidden");
  const opts = getFilterOptions() || { activities: [], conformance_rules: [] };
  const type = h("select", { name: "alert_type", disabled: !!rule }, Object.entries(TYPES).map(([k, v]) => h("option", { value: k, selected: rule?.alert_type === k }, v)));
  const name = h("input", { name: "name", required: true, maxlength: "200", value: rule?.name || "" });
  const severity = h("select", { name: "severity" }, ["low", "medium", "high"].map((s) => h("option", { value: s, selected: (rule?.severity || "medium") === s }, SEVERITY_LABEL[s])));
  const enabled = h("input", { type: "checkbox", checked: rule ? rule.enabled : true });
  const params = h("div", { class: "form-grid" });
  const draw = () => {
    const p = rule?.params || {};
    if (type.value === "duration_threshold") {
      clear(params,
        h("label", {}, t("al.f.measure"), h("select", { name: "metric" }, Object.entries(METRICS).map(([k, v]) => h("option", { value: k, selected: p.metric === k }, v)))),
        h("label", {}, t("al.f.threshold"), h("input", { type: "number", name: "threshold_hours", min: "0", step: "any", required: true, value: p.threshold_hours ?? 720 })));
    } else if (type.value === "rework") {
      clear(params, h("label", {}, t("al.f.activity_optional"), h("select", { name: "activity" }, h("option", { value: "" }, t("al.f.any_repeated")),
        opts.activities.map((a) => h("option", { value: a, selected: p.activity === a }, a)))));
    } else {
      clear(params, opts.conformance_rules.length
        ? h("label", {}, t("al.f.conformance_rule"), h("select", { name: "conformance_rule_id", required: true },
          opts.conformance_rules.map((r) => h("option", { value: r.id, selected: rule?.conformance_rule_id === r.id }, r.name))))
        : h("p", { class: "notice warn small" }, t("al.need_conformance_rule")));
    }
  };
  type.addEventListener("change", draw);
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const body = { name: name.value, alert_type: type.value, severity: severity.value, enabled: enabled.checked, params: {} };
    if (type.value === "duration_threshold") {
      body.params = { metric: form.elements.metric.value, threshold_hours: Number(form.elements.threshold_hours.value) };
    } else if (type.value === "rework") {
      body.params = { activity: form.elements.activity.value || null };
    } else {
      if (!form.elements.conformance_rule_id) return toast(t("al.need_conformance_rule"), "error");
      body.conformance_rule_id = Number(form.elements.conformance_rule_id.value);
    }
    const ds = currentDatasetId();
    try {
      if (rule) await api(`/api/datasets/${ds}/alert-rules/${rule.id}`, { method: "PUT", body });
      else await api(`/api/datasets/${ds}/alert-rules`, { method: "POST", body });
      toast(rule ? t("al.rule_updated") : t("al.rule_created"));
      card.classList.add("hidden");
      render();
    } catch (err) { toast(err.message, "error"); }
  } },
  h("div", { class: "form-grid" }, h("label", {}, t("common.name"), name), h("label", {}, t("cf.type"), type), h("label", {}, t("al.severity"), severity),
    h("label", { class: "inline" }, enabled, t("common.enabled"))),
  params,
  h("div", { class: "actions", style: { marginTop: "12px" } }, h("button", { class: "btn btn-primary", type: "submit" }, rule ? t("common.save") : t("common.create")),
    h("button", { class: "btn", type: "button", onclick: () => card.classList.add("hidden") }, t("common.cancel"))));
  clear(card, h("h2", {}, rule ? t("al.edit_title", { name: rule.name }) : t("al.new_rule")), form);
  draw();
  reveal(card);
}

async function setStatus(alert, status) {
  try {
    await api(`/api/alerts/${alert.id}`, { method: "PATCH", body: { status } });
    toast(t("al.status_changed", { status: STATUS[status] }));
    refreshAlertBadge();
    loadAlerts();
  } catch (err) { toast(err.message, "error"); }
}

async function loadAlerts() {
  const ds = currentDatasetId();
  const box = $("#alerts-root");
  loading(box);
  const params = new URLSearchParams({ dataset_id: ds, page, page_size: 25 });
  const st = $("#status-filter").value;
  if (st) params.append("status", st);
  if ($("#active-only").checked) params.set("active_only", "true");
  $("#alerts-export").href = `/api/datasets/${ds}/exports/alerts.csv`;
  let res;
  try { res = await api("/api/alerts", { params }); } catch (err) { return showError(box, err); }
  if (!res.total) return clear(box, h("div", { class: "state" }, h("div", { class: "title" }, st ? t("al.none_status", { status: STATUS[st] }) : t("al.none")), t("al.none_hint")));
  clear(box, table([
    { key: "severity", label: t("al.severity"), render: (a) => badge(SEVERITY_LABEL[a.severity] || a.severity, a.severity) },
    { key: "status", label: t("common.status"), render: (a) => badge(STATUS[a.status] || a.status, a.status) },
    { key: "rule", label: t("cf.rule"), render: (a) => a.rule_name },
    { key: "case", label: t("common.case"), render: (a) => h("a", { class: "mono", href: linkWith("/cases", { filters: {}, extra: { case: a.case_id } }) }, a.case_id) },
    { key: "message", label: t("finding.col.message"), render: (a) => h("div", {}, a.message, a.condition_active ? null : h("div", { class: "small muted" }, t("al.condition_gone"))) },
    { key: "detected", label: t("al.first_detected"), render: (a) => fmtTs(`${a.first_detected_at}Z`, "UTC") },
    { key: "actions", label: "", render: (a) => h("div", { class: "actions" },
      a.status !== "acknowledged" && a.status !== "resolved" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "acknowledged") }, t("al.acknowledge")) : null,
      a.status !== "resolved" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "resolved") }, t("al.resolve")) : null,
      a.status !== "open" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "open") }, t("al.reopen")) : null) },
  ], res.items), pager({ page: res.page, pageSize: res.page_size, total: res.total }, (p) => { page = p; loadAlerts(); }));
  reveal(box.querySelector("tbody"), { stagger: 25 });
}

async function render() {
  const ds = currentDatasetId();
  const root = $("#alert-rules");
  if (!ds) { clear($("#alerts-root")); return noDatasetState(root); }
  loading(root);
  let rules, crules, counts;
  try {
    [rules, crules, counts] = await Promise.all([
      api(`/api/datasets/${ds}/alert-rules`), api(`/api/datasets/${ds}/conformance-rules`), api("/api/alerts/counts", { params: { dataset_id: ds } })]);
  } catch (err) { return showError(root, err); }
  clear(root,
    h("p", { class: "small muted" }, t("al.counts", { open: fmtInt(counts.open), acknowledged: fmtInt(counts.acknowledged), resolved: fmtInt(counts.resolved) })),
    rules.length ? table([
      { key: "name", label: t("cf.rule"), render: (r) => h("div", {}, h("b", {}, r.name), h("div", { class: "small muted" }, describe(r, crules))) },
      { key: "type", label: t("cf.type"), render: (r) => TYPES[r.alert_type] },
      { key: "severity", label: t("al.severity"), render: (r) => badge(SEVERITY_LABEL[r.severity] || r.severity, r.severity) },
      { key: "enabled", label: t("common.enabled"), render: (r) => (r.enabled ? badge(t("common.enabled"), "ok") : badge(t("common.disabled"))) },
      { key: "last", label: t("al.last_evaluated"), render: (r) => (r.last_evaluated_at ? fmtTs(`${r.last_evaluated_at}Z`, "UTC") : t("common.never")) },
      { key: "actions", label: "", render: (r) => h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-sm", onclick: () => openEditor(r) }, t("common.edit")),
        h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => {
          if (!confirm(t("al.delete_confirm", { name: r.name }))) return;
          try { await api(`/api/datasets/${ds}/alert-rules/${r.id}`, { method: "DELETE" }); toast(t("al.rule_deleted")); refreshAlertBadge(); render(); } catch (err) { toast(err.message, "error"); }
        } }, t("common.delete"))) },
    ], rules) : h("div", { class: "state" }, h("div", { class: "title" }, t("al.no_rules.title")), t("al.no_rules.hint")));
  page = 1;
  loadAlerts();
}

$("#evaluate-now").addEventListener("click", async () => {
  const ds = currentDatasetId();
  if (!ds) return toast(t("common.load_dataset_first"), "error");
  const btn = $("#evaluate-now");
  btn.disabled = true;
  try {
    const r = await api(`/api/datasets/${ds}/alert-rules/evaluate`, { method: "POST" });
    clear($("#eval-result"), h("div", { class: "notice ok" }, h("b", {}, t("al.eval_done")), " ",
      t("al.eval_detail", { rules: r.rules_evaluated, created: fmtInt(r.created), updated: fmtInt(r.updated), gone: fmtInt(r.no_longer_matching) })));
    refreshAlertBadge();
    render();
  } catch (err) { showError($("#eval-result"), err); } finally { btn.disabled = false; }
});
$("#new-alert-rule").addEventListener("click", () => (currentDatasetId() ? openEditor() : toast(t("common.load_dataset_first"), "error")));
$("#status-filter").addEventListener("change", () => { page = 1; loadAlerts(); });
$("#active-only").addEventListener("change", () => { page = 1; loadAlerts(); });
bootPage(render, { filters: false });
