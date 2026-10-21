// Task mining: import user-interaction events, sessionize, and analyse sequences.
import {
  $, $$, api, badge, bootPage, clear, countUp, fmtDur, fmtInt, fmtNum, fmtPct, fmtTs, getDatasets, h, reveal,
  showError, t, table, toast,
} from "./core.js";
import { renderModel } from "./models.js";

const FIELDS = ["session_id", "timestamp", "application", "action", "user_id", "business_case_id", "duration"];
const REQUIRED = new Set(["timestamp", "application", "action", "user_id"]);
let logs = [];
let current = null;
let tab = "variants";
let pendingFile = null;
let casePage = 1;

const sp = () => new URLSearchParams(location.search);
const sess = () => ({ mode: $("#sess-mode").value, gap_minutes: Number($("#sess-gap").value) || 30 });
const gran = () => $("#granularity").value;
const seqText = (labels) => labels.join(" → ");

function setUrl() {
  const p = new URLSearchParams();
  if (current) p.set("log", current.id);
  if (tab !== "variants") p.set("tab", tab);
  const s = sess();
  if (s.mode !== "source_and_inactivity") p.set("mode", s.mode);
  if (s.gap_minutes !== 30) p.set("gap", s.gap_minutes);
  if (gran() !== "action") p.set("granularity", gran());
  history.replaceState(null, "", `${location.pathname}${p.toString() ? `?${p}` : ""}`);
}

function findings(list) {
  if (!list?.length) return h("p", { class: "small muted" }, t("task.no_findings"));
  return h("details", {}, h("summary", {}, t("task.findings_count", { n: fmtInt(list.length) })), table([
    { key: "severity", label: t("ocel.severity"), render: (f) => badge(t(`severity.${f.severity}`), f.severity) },
    { key: "row", label: t("task.row"), num: true, render: (f) => (f.row ? fmtInt(f.row) : "—") },
    { key: "message", label: t("ocel.message"), render: (f) => t(f.key, f.params || {}) },
  ], list.slice(0, 200)));
}

async function loadLogs() {
  logs = (await api("/api/tasks")).items;
  const sel = $("#log-select");
  if (!logs.length) {
    clear(sel, h("option", { value: "" }, t("task.no_logs")));
    current = null;
    $("#task-body").classList.add("hidden");
    clear($("#log-info"), h("p", { class: "muted" }, t("task.empty_hint")));
    return;
  }
  clear(sel, logs.map((l) => h("option", { value: l.id }, `${l.name} (#${l.id})`)));
  const want = Number(sp().get("log")) || logs[0].id;
  current = logs.find((l) => l.id === want) || logs[0];
  sel.value = current.id;
}

function drawInfo() {
  const c = current.counts;
  clear($("#log-info"),
    h("div", { class: "chips" }, current.is_demo ? badge(t("ocel.demo_badge"), "synthetic") : null,
      badge(`${t("task.users")}: ${fmtInt(c.users)}`), badge(`${t("task.applications")}: ${fmtInt(c.applications)}`)),
    current.description ? h("p", { class: "muted" }, current.description) : null,
    h("p", { class: "muted" }, t("task.source_line", { file: current.source_filename, sha: current.source_sha256.slice(0, 12), when: fmtTs(current.imported_at, "UTC") })),
    current.ignored_columns.length ? h("p", { class: "small notice info" }, t("task.ignored_columns", { columns: current.ignored_columns.join(", ") })) : null,
    findings(current.findings));
}

function kpi(label, value, sub, fmt = fmtInt) {
  const v = h("div", { class: "value" }, value === null || value === undefined ? "—" : fmt(value));
  if (typeof value === "number" && fmt === fmtInt) countUp(v, value, fmt);
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), v, sub ? h("div", { class: "sub" }, sub) : null);
}

async function drawSummary() {
  const s = await api(`/api/tasks/${current.id}/summary`, { params: sess() });
  clear($("#kpis"),
    kpi(t("task.sessions"), s.sessions, t("task.users_sub", { n: fmtInt(s.users) })),
    kpi(t("task.events"), s.events),
    kpi(t("task.median_events_per_session"), s.median_events_per_session, null, (v) => fmtNum(v, 1)),
    kpi(t("task.median_elapsed_span"), s.median_elapsed_span_s, t("task.elapsed_sub"), fmtDur),
    kpi(t("task.explicit_duration_total"), s.explicit_duration_total_s, t("task.coverage", { pct: fmtPct(s.explicit_duration_coverage ?? 0, 0) }), fmtDur),
    kpi(t("task.sessions_with_cases"), s.sessions_linked_to_cases, t("task.cases_sub", { n: fmtInt(s.business_cases) })));
  reveal($("#kpis"));
}

async function drawVariants() {
  const box = $("#variants-view");
  try {
    const r = await api(`/api/tasks/${current.id}/variants`, { params: { ...sess(), granularity: gran(), limit: 50 } });
    clear(box, h("p", { class: "small muted" }, t("task.variants_summary", { n: fmtInt(r.variant_count), sessions: fmtInt(r.sessions) })),
      table([
        { key: "seq", label: t("task.sequence"), render: (v) => h("span", { class: "small" }, seqText(v.sequence)) },
        { key: "sessions", label: t("task.sessions"), num: true, render: (v) => fmtInt(v.sessions) },
        { key: "share", label: t("task.share"), num: true, render: (v) => fmtPct(v.share, 1) },
        { key: "users", label: t("task.users"), num: true, render: (v) => fmtInt(v.users) },
        { key: "span", label: t("task.median_elapsed_span"), num: true, render: (v) => fmtDur(v.median_elapsed_span_s) },
        { key: "cov", label: t("task.duration_coverage"), num: true, render: (v) => fmtPct(v.explicit_duration_coverage, 0) },
      ], r.variants));
  } catch (err) { showError(box, err); }
}

async function drawSequences() {
  const box = $("#sequences-view");
  try {
    const r = await api(`/api/tasks/${current.id}/sequences`, { params: { ...sess(), granularity: gran(),
      min_length: Number($("#seq-min").value) || 2, max_length: Number($("#seq-max").value) || 5,
      min_sessions: Number($("#seq-sessions").value) || 2 } });
    clear(box, r.sequences.length ? table([
      { key: "seq", label: t("task.sequence"), render: (v) => h("span", { class: "small" }, seqText(v.sequence)) },
      { key: "length", label: t("task.length"), num: true, render: (v) => fmtInt(v.length) },
      { key: "occ", label: t("task.occurrences"), num: true, render: (v) => fmtInt(v.occurrences) },
      { key: "sessions", label: t("task.sessions"), num: true, render: (v) => fmtInt(v.sessions) },
      { key: "users", label: t("task.users"), num: true, render: (v) => fmtInt(v.users) },
    ], r.sequences) : h("p", { class: "muted" }, t("task.no_sequences")));
  } catch (err) { showError(box, err); }
}

async function drawSwitches() {
  const box = $("#switches-view");
  try {
    const r = await api(`/api/tasks/${current.id}/switches`, { params: sess() });
    clear(box,
      h("p", {}, t("task.switch_summary", { switches: fmtInt(r.switches), transitions: fmtInt(r.transitions),
        share: fmtPct(r.switch_share ?? 0, 1), sessions: fmtInt(r.sessions_with_switch), total: fmtInt(r.sessions),
        median: fmtNum(r.median_switches_per_session, 1) })),
      r.pairs.length ? table([
        { key: "from", label: t("model.from") },
        { key: "to", label: t("model.to") },
        { key: "switches", label: t("task.switches"), num: true, render: (p) => fmtInt(p.switches) },
      ], r.pairs) : h("p", { class: "muted" }, t("task.no_switches")));
  } catch (err) { showError(box, err); }
}

async function drawDfg() {
  const box = $("#dfg-view");
  try {
    const r = await api(`/api/tasks/${current.id}/dfg`, { params: { ...sess(), granularity: gran() } });
    renderModel(box, r, { height: 560 });
  } catch (err) { showError(box, err); }
}

function drawLinkSelect() {
  const sel = $("#link-dataset");
  clear(sel, h("option", { value: "" }, t("task.no_link")), getDatasets().map((d) => h("option", { value: d.id }, d.name)));
  sel.value = current.linked_dataset_id ?? "";
}

async function drawCases() {
  const box = $("#cases-view");
  try {
    const r = await api(`/api/tasks/${current.id}/cases`, { params: { ...sess(), page: casePage, page_size: 25 } });
    const pages = Math.max(1, Math.ceil(r.cases / r.page_size));
    const caseCell = (c) => (r.dataset_id && c.in_dataset
      ? h("a", { class: "mono", href: `/cases?${new URLSearchParams({ dataset: r.dataset_id, case: c.business_case_id })}` }, c.business_case_id)
      : h("span", { class: "mono" }, c.business_case_id));
    clear(box,
      h("p", { class: "small" }, t("task.cases_summary", { cases: fmtInt(r.cases), linked: fmtInt(r.linked_events), unlinked: fmtInt(r.unlinked_events) }),
        r.dataset_id ? ` ${t("task.matched", { n: fmtInt(r.matched_in_dataset), name: r.dataset_name })}` : ` ${t("task.not_linked_hint")}`),
      table([
        { key: "case", label: t("common.case"), render: caseCell },
        { key: "sessions", label: t("task.sessions"), num: true, render: (c) => fmtInt(c.sessions) },
        { key: "events", label: t("task.events"), num: true, render: (c) => fmtInt(c.events) },
        { key: "users", label: t("task.users"), num: true, render: (c) => fmtInt(c.users) },
        { key: "apps", label: t("task.applications"), render: (c) => h("span", { class: "small" }, c.applications.join(", ")) },
        { key: "dur", label: t("task.explicit_duration"), num: true, render: (c) => (c.explicit_duration_s === null ? "—" : `${fmtDur(c.explicit_duration_s)} (${fmtInt(c.events_with_duration)}/${fmtInt(c.events)})`) },
        { key: "in", label: t("task.in_dataset"), render: (c) => (c.in_dataset === null ? "—" : c.in_dataset ? badge(t("common.yes"), "ok") : badge(t("common.no"), "warn")) },
      ], r.items),
      h("div", { class: "actions small" },
        h("button", { type: "button", class: "btn btn-sm", disabled: casePage <= 1, onclick: () => { casePage -= 1; drawCases(); } }, "‹"),
        t("ocel.page_of", { page: fmtInt(casePage), pages: fmtInt(pages) }),
        h("button", { type: "button", class: "btn btn-sm", disabled: casePage >= pages, onclick: () => { casePage += 1; drawCases(); } }, "›")));
  } catch (err) { showError(box, err); }
}

function selectTab(name) {
  tab = name;
  $$("#task-tabs .tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  setUrl();
  ({ variants: drawVariants, sequences: drawSequences, switches: drawSwitches, dfg: drawDfg, cases: drawCases })[name]?.();
}

async function refresh() {
  if (!current) return;
  setUrl();
  await drawSummary().catch((err) => showError($("#kpis"), err));
  selectTab(tab);
}

async function render() {
  try { await loadLogs(); } catch (err) { showError($("#log-info"), err); return; }
  if (!current) return;
  const p = sp();
  $("#sess-mode").value = p.get("mode") || "source_and_inactivity";
  $("#sess-gap").value = p.get("gap") || 30;
  $("#granularity").value = p.get("granularity") || "action";
  tab = p.get("tab") || "variants";
  $("#task-body").classList.remove("hidden");
  drawInfo();
  drawLinkSelect();
  refresh();
}

function drawMapping(preview) {
  clear($("#mapping-fields"), FIELDS.map((f) => h("label", {}, h("span", {}, `${t(`task.field.${f}`)}${REQUIRED.has(f) ? " *" : ""}`),
    h("select", { name: f }, h("option", { value: "" }, t("task.not_mapped")),
      preview.columns.map((c) => h("option", { value: c, selected: preview.suggested_mapping[f] === c }, c))))));
  $("#mapping-form").classList.remove("hidden");
}

function importFailed(err) {
  clear($("#upload-status"), h("p", { class: "notice danger" }, err.message), Array.isArray(err.details) ? findings(err.details) : null);
  const d = $("#upload-status details");
  if (d) d.open = true;
}

async function afterImport(tl) {
  toast(t("task.imported", { name: tl.name, events: fmtInt(tl.counts.events) }));
  history.replaceState(null, "", `${location.pathname}?log=${tl.id}`);
  $("#mapping-form").classList.add("hidden");
  render();
}

$("#preview-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  pendingFile = $("#task-file").files[0];
  if (!pendingFile) return;
  const form = new FormData();
  form.append("file", pendingFile);
  try { clear($("#upload-status")); drawMapping(await api("/api/tasks/preview", { method: "POST", form })); } catch (err) { importFailed(err); }
});
$("#mapping-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const mapping = {};
  for (const sel of $$("#mapping-fields select")) if (sel.value) mapping[sel.name] = sel.value;
  const form = new FormData();
  form.append("file", pendingFile);
  form.append("mapping", JSON.stringify(mapping));
  form.append("source_timezone", $("#task-tz").value || "UTC");
  form.append("invalid_row_policy", $("#task-policy").value);
  try { clear($("#upload-status")); afterImport(await api("/api/tasks/upload", { method: "POST", form })); } catch (err) { importFailed(err); }
});
$("#load-demo").addEventListener("click", async () => {
  $("#load-demo").disabled = true;
  try { afterImport(await api("/api/tasks/demo", { method: "POST" })); } catch (err) { importFailed(err); } finally { $("#load-demo").disabled = false; }
});
$("#log-select").addEventListener("change", (e) => { history.replaceState(null, "", `${location.pathname}?log=${e.target.value}`); render(); });
$("#delete-log").addEventListener("click", async () => {
  if (!current || !confirm(t("task.confirm_delete", { name: current.name }))) return;
  try { await api(`/api/tasks/${current.id}`, { method: "DELETE" }); history.replaceState(null, "", location.pathname); render(); } catch (err) { toast(err.message, "error"); }
});
$("#link-dataset").addEventListener("change", async (e) => {
  try {
    current = await api(`/api/tasks/${current.id}/link`, { method: "PUT", body: { dataset_id: e.target.value ? Number(e.target.value) : null } });
    casePage = 1;
    drawCases();
  } catch (err) { toast(err.message, "error"); }
});
for (const id of ["#sess-mode", "#sess-gap", "#granularity"]) $(id).addEventListener("change", () => { casePage = 1; refresh(); });
for (const id of ["#seq-min", "#seq-max", "#seq-sessions"]) $(id).addEventListener("change", drawSequences);
$$("#task-tabs .tab").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
bootPage(render, { filters: false });
