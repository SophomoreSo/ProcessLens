import {
  $, api, badge, bootPage, clear, currentDatasetId, fmtInt, fmtTs, getFilterOptions, h, linkWith, loading,
  noDatasetState, pager, refreshAlertBadge, showError, table, toast,
} from "./core.js";

const TYPES = {
  duration_threshold: "Duration threshold exceeded",
  rework: "Rework detected",
  conformance_violation: "Conformance violation",
};
const METRICS = {
  observed_duration: "Observed duration (all cases)",
  completed_throughput: "Completed throughput (completed cases)",
  open_case_age: "Open-case age at reference time (incomplete/unknown cases)",
};
let page = 1;

function describe(rule, crules) {
  const p = rule.params || {};
  if (rule.alert_type === "duration_threshold") return `${METRICS[p.metric] || p.metric} > ${p.threshold_hours} h`;
  if (rule.alert_type === "rework") return p.activity ? `'${p.activity}' occurs more than once` : "any activity repeated";
  const cr = crules.find((r) => r.id === rule.conformance_rule_id);
  return `violates “${cr ? cr.name : `rule ${rule.conformance_rule_id}`}”`;
}

function openEditor(rule = null) {
  const card = $("#alert-rule-editor");
  card.classList.remove("hidden");
  const opts = getFilterOptions() || { activities: [], conformance_rules: [] };
  const type = h("select", { name: "alert_type", disabled: !!rule }, Object.entries(TYPES).map(([k, v]) => h("option", { value: k, selected: rule?.alert_type === k }, v)));
  const name = h("input", { name: "name", required: true, maxlength: "200", value: rule?.name || "" });
  const severity = h("select", { name: "severity" }, ["low", "medium", "high"].map((s) => h("option", { value: s, selected: (rule?.severity || "medium") === s }, s)));
  const enabled = h("input", { type: "checkbox", checked: rule ? rule.enabled : true });
  const params = h("div", { class: "form-grid" });
  const draw = () => {
    const p = rule?.params || {};
    if (type.value === "duration_threshold") {
      clear(params,
        h("label", {}, "Measure", h("select", { name: "metric" }, Object.entries(METRICS).map(([k, v]) => h("option", { value: k, selected: p.metric === k }, v)))),
        h("label", {}, "Threshold (hours)", h("input", { type: "number", name: "threshold_hours", min: "0", step: "any", required: true, value: p.threshold_hours ?? 720 })));
    } else if (type.value === "rework") {
      clear(params, h("label", {}, "Activity (optional)", h("select", { name: "activity" }, h("option", { value: "" }, "Any repeated activity"),
        opts.activities.map((a) => h("option", { value: a, selected: p.activity === a }, a)))));
    } else {
      clear(params, opts.conformance_rules.length
        ? h("label", {}, "Conformance rule", h("select", { name: "conformance_rule_id", required: true },
          opts.conformance_rules.map((r) => h("option", { value: r.id, selected: rule?.conformance_rule_id === r.id }, r.name))))
        : h("p", { class: "notice warn small" }, "Create a conformance rule first (Conformance page)."));
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
      if (!form.elements.conformance_rule_id) return toast("Create a conformance rule first.", "error");
      body.conformance_rule_id = Number(form.elements.conformance_rule_id.value);
    }
    const ds = currentDatasetId();
    try {
      if (rule) await api(`/api/datasets/${ds}/alert-rules/${rule.id}`, { method: "PUT", body });
      else await api(`/api/datasets/${ds}/alert-rules`, { method: "POST", body });
      toast(rule ? "Alert rule updated" : "Alert rule created — click “Evaluate rules now” to generate alerts.");
      card.classList.add("hidden");
      render();
    } catch (err) { toast(err.message, "error"); }
  } },
  h("div", { class: "form-grid" }, h("label", {}, "Name", name), h("label", {}, "Type", type), h("label", {}, "Severity", severity),
    h("label", { class: "inline" }, enabled, "Enabled")),
  params,
  h("div", { class: "actions", style: { marginTop: "12px" } }, h("button", { class: "btn btn-primary", type: "submit" }, rule ? "Save" : "Create"),
    h("button", { class: "btn", type: "button", onclick: () => card.classList.add("hidden") }, "Cancel")));
  clear(card, h("h2", {}, rule ? `Edit alert rule “${rule.name}”` : "New alert rule"), form);
  draw();
}

async function setStatus(alert, status) {
  try {
    await api(`/api/alerts/${alert.id}`, { method: "PATCH", body: { status } });
    toast(`Alert ${status}`);
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
  if (!res.total) return clear(box, h("div", { class: "state" }, h("div", { class: "title" }, `No ${st || ""} alerts`), "Evaluate the alert rules to check for new alerts."));
  clear(box, table([
    { key: "severity", label: "Severity", render: (a) => badge(a.severity, a.severity) },
    { key: "status", label: "Status", render: (a) => badge(a.status, a.status) },
    { key: "rule", label: "Rule", render: (a) => a.rule_name },
    { key: "case", label: "Case", render: (a) => h("a", { class: "mono", href: linkWith("/cases", { filters: {}, extra: { case: a.case_id } }) }, a.case_id) },
    { key: "message", label: "Message", render: (a) => h("div", {}, a.message, a.condition_active ? null : h("div", { class: "small muted" }, "Condition no longer met at the last evaluation.")) },
    { key: "detected", label: "First detected (UTC)", render: (a) => fmtTs(`${a.first_detected_at}Z`, "UTC") },
    { key: "actions", label: "", render: (a) => h("div", { class: "actions" },
      a.status !== "acknowledged" && a.status !== "resolved" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "acknowledged") }, "Acknowledge") : null,
      a.status !== "resolved" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "resolved") }, "Resolve") : null,
      a.status !== "open" ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(a, "open") }, "Reopen") : null) },
  ], res.items), pager({ page: res.page, pageSize: res.page_size, total: res.total }, (p) => { page = p; loadAlerts(); }));
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
    h("p", { class: "small muted" }, `This dataset: ${fmtInt(counts.open)} open · ${fmtInt(counts.acknowledged)} acknowledged · ${fmtInt(counts.resolved)} resolved alerts.`),
    rules.length ? table([
      { key: "name", label: "Rule", render: (r) => h("div", {}, h("b", {}, r.name), h("div", { class: "small muted" }, describe(r, crules))) },
      { key: "type", label: "Type", render: (r) => TYPES[r.alert_type] },
      { key: "severity", label: "Severity", render: (r) => badge(r.severity, r.severity) },
      { key: "enabled", label: "Enabled", render: (r) => (r.enabled ? badge("enabled", "ok") : badge("disabled")) },
      { key: "last", label: "Last evaluated (UTC)", render: (r) => (r.last_evaluated_at ? fmtTs(`${r.last_evaluated_at}Z`, "UTC") : "never") },
      { key: "actions", label: "", render: (r) => h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-sm", onclick: () => openEditor(r) }, "Edit"),
        h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => {
          if (!confirm(`Delete alert rule “${r.name}” and its alerts?`)) return;
          try { await api(`/api/datasets/${ds}/alert-rules/${r.id}`, { method: "DELETE" }); toast("Alert rule deleted"); refreshAlertBadge(); render(); } catch (err) { toast(err.message, "error"); }
        } }, "Delete")) },
    ], rules) : h("div", { class: "state" }, h("div", { class: "title" }, "No alert rules"), "Create one to start tracking cases."));
  page = 1;
  loadAlerts();
}

$("#evaluate-now").addEventListener("click", async () => {
  const ds = currentDatasetId();
  if (!ds) return toast("Load a dataset first.", "error");
  const btn = $("#evaluate-now");
  btn.disabled = true;
  try {
    const r = await api(`/api/datasets/${ds}/alert-rules/evaluate`, { method: "POST" });
    clear($("#eval-result"), h("div", { class: "notice ok" }, h("b", {}, "Evaluation finished. "),
      `${r.rules_evaluated} rule(s) evaluated: ${fmtInt(r.created)} new alert(s), ${fmtInt(r.updated)} existing alert(s) updated, ${fmtInt(r.no_longer_matching)} no longer matching.`));
    refreshAlertBadge();
    render();
  } catch (err) { showError($("#eval-result"), err); } finally { btn.disabled = false; }
});
$("#new-alert-rule").addEventListener("click", () => (currentDatasetId() ? openEditor() : toast("Load a dataset first.", "error")));
$("#status-filter").addEventListener("change", () => { page = 1; loadAlerts(); });
$("#active-only").addEventListener("change", () => { page = 1; loadAlerts(); });
bootPage(render, { filters: false });
