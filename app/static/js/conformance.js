import {
  $, api, applyFilters, badge, bootPage, clear, currentDatasetId, filterParams, fmtInt, fmtPct, getFilterOptions, h,
  linkWith, loading, noDatasetState, pager, readFilters, RESULT_LABEL, showError, STATUS_LABEL, table, toast,
} from "./core.js";

const RULE_TYPES = {
  required_activity: { label: "Required activity", fields: ["activity", "assume_complete"],
    help: "Compliant if the activity occurs. Absent in a complete case → violating; absent in an open case → not evaluable." },
  forbidden_activity: { label: "Forbidden activity", fields: ["activity", "assume_complete"],
    help: "Violating if the activity occurs at least once. Absent in an open case → not evaluable." },
  precedence: { label: "A must precede B", fields: ["activity_a", "activity_b", "assume_complete"],
    help: "Every occurrence of B needs an A with a strictly earlier timestamp. A tie with A → not evaluable. No B in a complete case → compliant (vacuous)." },
  max_duration: { label: "Maximum duration", fields: ["max_hours", "metric"],
    help: "Completed throughput: only completed cases are judged. Observed duration: every case as recorded so far." },
  max_occurrences: { label: "Maximum occurrences", fields: ["activity", "max_count", "assume_complete"],
    help: "Violating when the activity occurs more often than allowed (repeats count). Open cases under the limit → not evaluable." },
  start_activity: { label: "Expected start activity", fields: ["activities"],
    help: "The first event must be one of the activities. Several events tied at the first timestamp with mixed activities → not evaluable." },
  end_activity: { label: "Expected end activity", fields: ["activities", "assume_complete"],
    help: "Only complete cases are judged; the last event must be one of the activities." },
};
let rules = [];
let evalState = { rule: null, result: [], page: 1 };

function fieldInput(name, value, activities) {
  const actSelect = (n, multiple) => h("select", { name: n, multiple, required: true },
    multiple ? null : h("option", { value: "" }, "— choose —"),
    activities.map((a) => h("option", { value: a, selected: multiple ? (value || []).includes(a) : value === a }, a)));
  switch (name) {
    case "activity": return h("label", {}, "Activity", actSelect("activity", false));
    case "activity_a": return h("label", {}, "Activity A (must come first)", actSelect("activity_a", false));
    case "activity_b": return h("label", {}, "Activity B", actSelect("activity_b", false));
    case "activities": return h("label", {}, "Allowed activities", actSelect("activities", true));
    case "max_hours": return h("label", {}, "Maximum hours", h("input", { type: "number", name: "max_hours", min: "0", step: "any", required: true, value: value ?? "" }));
    case "max_count": return h("label", {}, "Maximum count", h("input", { type: "number", name: "max_count", min: "0", step: "1", required: true, value: value ?? 1 }));
    case "metric": return h("label", {}, "Duration measure", h("select", { name: "metric" },
      h("option", { value: "completed_throughput", selected: value !== "observed_duration" }, "Completed throughput (completed cases)"),
      h("option", { value: "observed_duration", selected: value === "observed_duration" }, "Observed duration (all cases, as recorded)")));
    case "assume_complete": return h("label", { class: "inline" }, h("input", { type: "checkbox", name: "assume_complete", checked: !!value }),
      "Treat incomplete/unknown cases as complete");
    default: return null;
  }
}

function openEditor(rule = null) {
  const card = $("#rule-editor");
  card.classList.remove("hidden");
  const activities = getFilterOptions()?.activities || [];
  const typeSel = h("select", { name: "rule_type", disabled: !!rule },
    Object.entries(RULE_TYPES).map(([k, v]) => h("option", { value: k, selected: rule ? rule.rule_type === k : k === "required_activity" }, v.label)));
  const name = h("input", { name: "name", required: true, maxlength: "200", value: rule?.name || "" });
  const enabled = h("input", { type: "checkbox", name: "enabled", checked: rule ? rule.enabled : true });
  const paramsBox = h("div", { class: "form-grid" });
  const help = h("p", { class: "small muted" });
  const drawParams = () => {
    const t = RULE_TYPES[typeSel.value];
    help.textContent = t.help;
    clear(paramsBox, t.fields.map((f) => fieldInput(f, rule?.params?.[f], activities)));
  };
  typeSel.addEventListener("change", drawParams);
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const t = RULE_TYPES[typeSel.value];
    const params = {};
    for (const f of t.fields) {
      const el = form.elements[f];
      if (f === "activities") params[f] = [...el.selectedOptions].map((o) => o.value);
      else if (f === "assume_complete") params[f] = el.checked;
      else if (f === "max_hours") params[f] = Number(el.value);
      else if (f === "max_count") params[f] = parseInt(el.value, 10);
      else params[f] = el.value;
    }
    const body = { name: name.value, rule_type: typeSel.value, params, enabled: enabled.checked };
    const ds = currentDatasetId();
    try {
      const saved = rule
        ? await api(`/api/datasets/${ds}/conformance-rules/${rule.id}`, { method: "PUT", body })
        : await api(`/api/datasets/${ds}/conformance-rules`, { method: "POST", body });
      toast(rule ? "Rule updated" : "Rule created");
      card.classList.add("hidden");
      await render();
      evaluate(saved);
    } catch (err) { toast(err.message, "error"); }
  } },
  h("div", { class: "form-grid" }, h("label", {}, "Name", name), h("label", {}, "Rule type", typeSel),
    h("label", { class: "inline" }, enabled, "Enabled")),
  help, paramsBox,
  h("div", { class: "actions", style: { marginTop: "12px" } }, h("button", { type: "submit", class: "btn btn-primary" }, rule ? "Save rule" : "Create rule"),
    h("button", { type: "button", class: "btn", onclick: () => card.classList.add("hidden") }, "Cancel")));
  clear(card, h("h2", {}, rule ? `Edit rule “${rule.name}”` : "New conformance rule"), form);
  drawParams();
  card.scrollIntoView({ behavior: "smooth" });
}

async function removeRule(rule) {
  if (!confirm(`Delete rule “${rule.name}”? Alert rules based on it and their alerts are deleted too.`)) return;
  try {
    await api(`/api/datasets/${currentDatasetId()}/conformance-rules/${rule.id}`, { method: "DELETE" });
    toast("Rule deleted");
    if (evalState.rule?.id === rule.id) $("#evaluation").classList.add("hidden");
    render();
  } catch (err) { toast(err.message, "error"); }
}

async function evaluate(rule, page = 1) {
  evalState = { ...evalState, rule, page };
  const card = $("#evaluation");
  card.classList.remove("hidden");
  loading(card, `Evaluating “${rule.name}”…`);
  const params = filterParams();
  params.set("page", page);
  params.set("page_size", 25);
  for (const r of evalState.result) params.append("result", r);
  let res;
  try { res = await api(`/api/datasets/${currentDatasetId()}/conformance-rules/${rule.id}/evaluation`, { params }); } catch (err) { return showError(card, err); }
  const s = res.summary;
  const n = s.population_case_count || 1;
  const toggle = (r) => h("label", { class: "inline" }, h("input", { type: "checkbox", checked: evalState.result.includes(r), onchange: (e) => {
    evalState.result = e.target.checked ? [...evalState.result, r] : evalState.result.filter((x) => x !== r);
    evaluate(rule, 1);
  } }), RESULT_LABEL[r]);
  const exportQs = filterParams();
  exportQs.set("rule_id", rule.id);
  clear(card,
    h("div", { class: "card-head" }, h("h2", {}, `Evaluation: ${rule.name}`), h("span", { class: "small muted" }, rule.description)),
    h("div", { class: "kpis" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Cases evaluated"), h("div", { class: "value" }, fmtInt(s.population_case_count)), h("div", { class: "sub" }, "filtered population")),
      ...["compliant", "violating", "not_evaluable"].map((r) => h("div", { class: "kpi" }, h("div", { class: "label" }, RESULT_LABEL[r]),
        h("div", { class: "value" }, fmtInt(s[r])), h("div", { class: "sub" }, fmtPct(s[r] / n)),
        h("button", { type: "button", class: "btn btn-sm", style: { marginTop: "6px" }, disabled: !s[r], onclick: () => {
          applyFilters({ ...readFilters(), conformance_rule: String(rule.id), conformance_result: [r] });
          toast(`Global filter set: ${RESULT_LABEL[r]} cases for “${rule.name}”.`);
        } }, "Use as filter")))),
    h("div", { class: "actions", style: { margin: "10px 0" } }, h("span", { class: "muted small" }, "Show:"), toggle("violating"), toggle("not_evaluable"), toggle("compliant"),
      h("span", { class: "grow" }),
      h("a", { class: "btn btn-sm", download: "", href: `/api/datasets/${currentDatasetId()}/exports/conformance.csv?${exportQs}` }, "Export CSV")),
    table([
      { key: "case_id", label: "Case", render: (c) => h("a", { class: "mono", href: linkWith("/cases", { extra: { case: c.case_id } }) }, c.case_id) },
      { key: "result", label: "Result", render: (c) => badge(RESULT_LABEL[c.result], c.result) },
      { key: "status", label: "Case status", render: (c) => badge(STATUS_LABEL[c.status], c.status) },
      { key: "explanation", label: "Explanation" },
    ], res.cases.items),
    pager({ page: res.cases.page, pageSize: res.cases.page_size, total: res.cases.total }, (p) => evaluate(rule, p)),
    h("ul", { class: "small muted" }, res.notes.map((x) => h("li", {}, x))));
}

async function render() {
  const ds = currentDatasetId();
  const root = $("#rules-root");
  if (!ds) return noDatasetState(root);
  loading(root);
  let summary;
  try {
    summary = await api(`/api/datasets/${ds}/conformance/summary`, { params: filterParams() });
  } catch (err) { return showError(root, err); }
  rules = summary.rules;
  if (!rules.length) {
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, "No rules yet"), "Create a rule to check cases against your business requirements."));
  }
  clear(root,
    h("p", { class: "small muted" }, `Counts over ${fmtInt(summary.population_case_count)} filtered cases.`),
    table([
      { key: "name", label: "Rule", render: (r) => h("div", {}, h("b", {}, r.rule.name), h("div", { class: "small muted" }, r.rule.description)) },
      { key: "type", label: "Type", render: (r) => RULE_TYPES[r.rule.rule_type]?.label || r.rule.rule_type },
      { key: "c", label: "Compliant", num: true, render: (r) => (r.rule.enabled ? fmtInt(r.compliant) : "—") },
      { key: "v", label: "Violating", num: true, render: (r) => (r.rule.enabled ? h("b", {}, fmtInt(r.violating)) : "—") },
      { key: "n", label: "Not evaluable", num: true, render: (r) => (r.rule.enabled ? fmtInt(r.not_evaluable) : "—") },
      { key: "enabled", label: "Status", render: (r) => (r.rule.enabled ? badge("enabled", "ok") : badge("disabled")) },
      { key: "actions", label: "", render: (r) => h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-sm btn-primary", disabled: !r.rule.enabled, onclick: (e) => { e.stopPropagation(); evaluate(r.rule); } }, "Evaluate"),
        h("button", { type: "button", class: "btn btn-sm", onclick: (e) => { e.stopPropagation(); openEditor(r.rule); } }, "Edit"),
        h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: (e) => { e.stopPropagation(); removeRule(r.rule); } }, "Delete")) },
    ], rules));
  if (evalState.rule) {
    const again = rules.find((r) => r.rule.id === evalState.rule.id);
    if (again && again.rule.enabled) evaluate(again.rule, 1);
  }
}

$("#new-rule").addEventListener("click", () => {
  if (!currentDatasetId()) return toast("Load a dataset first.", "error");
  openEditor();
});
bootPage(render);
