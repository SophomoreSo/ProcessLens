import {
  $, api, applyFilters, badge, bootPage, IS_ADMIN, clear, currentDatasetId, filterParams, fmtInt, fmtPct, getFilterOptions, h,
  linkWith, loading, noDatasetState, pager, readFilters, RESULT_LABEL, reveal, showError, STATUS_LABEL, t, table, toast,
} from "./core.js";

const RULE_FIELDS = {
  required_activity: ["activity", "assume_complete"],
  forbidden_activity: ["activity", "assume_complete"],
  precedence: ["activity_a", "activity_b", "assume_complete"],
  max_duration: ["max_hours", "metric"],
  max_occurrences: ["activity", "max_count", "assume_complete"],
  start_activity: ["activities"],
  end_activity: ["activities", "assume_complete"],
};
const ruleLabel = (type) => t(`rule.type.${type}`);
const ruleHelp = (type) => t(`rule.help.${type}`);
let rules = [];
let evalState = { rule: null, result: [], page: 1 };

function fieldInput(name, value, activities) {
  const actSelect = (n, multiple) => h("select", { name: n, multiple, required: true },
    multiple ? null : h("option", { value: "" }, t("common.choose")),
    activities.map((a) => h("option", { value: a, selected: multiple ? (value || []).includes(a) : value === a }, a)));
  switch (name) {
    case "activity": return h("label", {}, t("field.activity"), actSelect("activity", false));
    case "activity_a": return h("label", {}, t("cf.f.activity_a"), actSelect("activity_a", false));
    case "activity_b": return h("label", {}, t("cf.f.activity_b"), actSelect("activity_b", false));
    case "activities": return h("label", {}, t("cf.f.activities"), actSelect("activities", true));
    case "max_hours": return h("label", {}, t("cf.f.max_hours"), h("input", { type: "number", name: "max_hours", min: "0", step: "any", required: true, value: value ?? "" }));
    case "max_count": return h("label", {}, t("cf.f.max_count"), h("input", { type: "number", name: "max_count", min: "0", step: "1", required: true, value: value ?? 1 }));
    case "metric": return h("label", {}, t("cf.f.metric"), h("select", { name: "metric" },
      h("option", { value: "completed_throughput", selected: value !== "observed_duration" }, t("cf.f.metric_throughput")),
      h("option", { value: "observed_duration", selected: value === "observed_duration" }, t("cf.f.metric_observed"))));
    case "assume_complete": return h("label", { class: "inline" }, h("input", { type: "checkbox", name: "assume_complete", checked: !!value }),
      t("cf.f.assume_complete"));
    default: return null;
  }
}

function openEditor(rule = null) {
  const card = $("#rule-editor");
  card.classList.remove("hidden");
  const activities = getFilterOptions()?.activities || [];
  const typeSel = h("select", { name: "rule_type", disabled: !!rule },
    Object.keys(RULE_FIELDS).map((k) => h("option", { value: k, selected: rule ? rule.rule_type === k : k === "required_activity" }, ruleLabel(k))));
  const name = h("input", { name: "name", required: true, maxlength: "200", value: rule?.name || "" });
  const enabled = h("input", { type: "checkbox", name: "enabled", checked: rule ? rule.enabled : true });
  const paramsBox = h("div", { class: "form-grid" });
  const help = h("p", { class: "small muted" });
  const drawParams = () => {
    help.textContent = ruleHelp(typeSel.value);
    clear(paramsBox, RULE_FIELDS[typeSel.value].map((f) => fieldInput(f, rule?.params?.[f], activities)));
  };
  typeSel.addEventListener("change", drawParams);
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const params = {};
    for (const f of RULE_FIELDS[typeSel.value]) {
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
      toast(rule ? t("cf.updated") : t("cf.created"));
      card.classList.add("hidden");
      await render();
      evaluate(saved);
    } catch (err) { toast(err.message, "error"); }
  } },
  h("div", { class: "form-grid" }, h("label", {}, t("common.name"), name), h("label", {}, t("cf.rule_type"), typeSel),
    h("label", { class: "inline" }, enabled, t("common.enabled"))),
  help, paramsBox,
  h("div", { class: "actions", style: { marginTop: "12px" } }, h("button", { type: "submit", class: "btn btn-primary" }, rule ? t("common.save") : t("common.create")),
    h("button", { type: "button", class: "btn", onclick: () => card.classList.add("hidden") }, t("common.cancel"))));
  clear(card, h("h2", {}, rule ? t("cf.edit_title", { name: rule.name }) : t("cf.new_title")), form);
  reveal(card);
  drawParams();
  card.scrollIntoView({ behavior: "smooth" });
}

async function removeRule(rule) {
  if (!confirm(t("cf.delete_confirm", { name: rule.name }))) return;
  try {
    await api(`/api/datasets/${currentDatasetId()}/conformance-rules/${rule.id}`, { method: "DELETE" });
    toast(t("cf.deleted"));
    if (evalState.rule?.id === rule.id) $("#evaluation").classList.add("hidden");
    render();
  } catch (err) { toast(err.message, "error"); }
}

async function evaluate(rule, page = 1) {
  evalState = { ...evalState, rule, page };
  const card = $("#evaluation");
  card.classList.remove("hidden");
  loading(card, t("cf.evaluating", { name: rule.name }));
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
    h("div", { class: "card-head" }, h("h2", {}, t("cf.evaluation_title", { name: rule.name })), h("span", { class: "small muted" }, rule.description)),
    h("div", { class: "kpis" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("cf.cases_evaluated")), h("div", { class: "value" }, fmtInt(s.population_case_count)), h("div", { class: "sub" }, t("cf.filtered_population"))),
      ...["compliant", "violating", "not_evaluable"].map((r) => h("div", { class: "kpi" }, h("div", { class: "label" }, RESULT_LABEL[r]),
        h("div", { class: "value" }, fmtInt(s[r])), h("div", { class: "sub" }, fmtPct(s[r] / n)),
        h("button", { type: "button", class: "btn btn-sm", style: { marginTop: "6px" }, disabled: !s[r], onclick: () => {
          applyFilters({ ...readFilters(), conformance_rule: String(rule.id), conformance_result: [r] });
          toast(t("cf.filter_set", { result: RESULT_LABEL[r], name: rule.name }));
        } }, t("cf.use_as_filter"))))),
    h("div", { class: "actions", style: { margin: "10px 0" } }, h("span", { class: "muted small" }, t("cf.show")), toggle("violating"), toggle("not_evaluable"), toggle("compliant"),
      h("span", { class: "grow" }),
      h("a", { class: "btn btn-sm", download: "", href: `/api/datasets/${currentDatasetId()}/exports/conformance.csv?${exportQs}` }, t("export.csv"))),
    table([
      { key: "case_id", label: t("common.case"), render: (c) => h("a", { class: "mono", href: linkWith("/cases", { extra: { case: c.case_id } }) }, c.case_id) },
      { key: "result", label: t("cf.result"), render: (c) => badge(RESULT_LABEL[c.result], c.result) },
      { key: "status", label: t("field.case_status"), render: (c) => badge(STATUS_LABEL[c.status], c.status) },
      { key: "explanation", label: t("cf.explanation") },
    ], res.cases.items),
    pager({ page: res.cases.page, pageSize: res.cases.page_size, total: res.cases.total }, (p) => evaluate(rule, p)),
    h("ul", { class: "small muted" }, res.notes.map((x) => h("li", {}, x))));
  reveal($(".kpis", card), { stagger: 40 });
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
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, t("cf.none.title")), t("cf.none.hint")));
  }
  clear(root,
    h("p", { class: "small muted" }, t("cf.counts_over", { n: fmtInt(summary.population_case_count) })),
    table([
      { key: "name", label: t("cf.rule"), render: (r) => h("div", {}, h("b", {}, r.rule.name), h("div", { class: "small muted" }, r.rule.description)) },
      { key: "type", label: t("cf.type"), render: (r) => ruleLabel(r.rule.rule_type) },
      { key: "c", label: RESULT_LABEL.compliant, num: true, render: (r) => (r.rule.enabled ? fmtInt(r.compliant) : "—") },
      { key: "v", label: RESULT_LABEL.violating, num: true, render: (r) => (r.rule.enabled ? h("b", {}, fmtInt(r.violating)) : "—") },
      { key: "n", label: RESULT_LABEL.not_evaluable, num: true, render: (r) => (r.rule.enabled ? fmtInt(r.not_evaluable) : "—") },
      { key: "enabled", label: t("common.status"), render: (r) => (r.rule.enabled ? badge(t("common.enabled"), "ok") : badge(t("common.disabled"))) },
      { key: "actions", label: "", render: (r) => h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-sm btn-primary", disabled: !r.rule.enabled, onclick: (e) => { e.stopPropagation(); evaluate(r.rule); } }, t("cf.evaluate")),
        IS_ADMIN ? h("button", { type: "button", class: "btn btn-sm", onclick: (e) => { e.stopPropagation(); openEditor(r.rule); } }, t("common.edit")) : null,
        IS_ADMIN ? h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: (e) => { e.stopPropagation(); removeRule(r.rule); } }, t("common.delete")) : null) },
    ], rules));
  reveal(root.querySelector("tbody"), { stagger: 35 });
  if (evalState.rule) {
    const again = rules.find((r) => r.rule.id === evalState.rule.id);
    if (again && again.rule.enabled) evaluate(again.rule, 1);
  }
}

$("#new-rule")?.addEventListener("click", () => {
  if (!currentDatasetId()) return toast(t("common.load_dataset_first"), "error");
  openEditor();
});
bootPage(render);
