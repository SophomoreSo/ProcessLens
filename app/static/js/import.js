import {
  $, $$, api, badge, bootPage, clear, fmtInt, getDatasets, h, pageParam, refreshAlertBadge, refreshDatasets, reveal,
  showError, t, table, toast,
} from "./core.js";
import { findingsTable } from "./findings.js";
import { followJob } from "./jobs.js";

const FIELDS = [
  ["case_id", t("field.case_id"), true], ["activity", t("field.activity"), true], ["timestamp", t("field.timestamp"), true],
  ["start_timestamp", t("field.start_timestamp"), false], ["event_id", t("field.event_id"), false], ["resource", t("field.resource"), false],
  ["department", t("field.department"), false], ["cost", t("field.cost"), false], ["case_status", t("field.case_status"), false],
];
const state = { preview: null, validation: null, step: 1 };

function goto(step) {
  state.step = step;
  for (const p of $$("[data-panel]")) p.classList.toggle("hidden", Number(p.dataset.panel) > step);
  for (const li of $$("#steps li")) {
    const s = Number(li.dataset.step);
    li.classList.toggle("active", s === step);
    li.classList.toggle("done", s < step);
  }
  const panel = $(`[data-panel="${step}"]`);
  panel?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (panel) reveal(panel, { stagger: 30 });
}

async function upload(file) {
  const status = $("#upload-status");
  if (!file) return;
  clear(status, h("div", { class: "notice info" }, h("span", { class: "spinner" }), " ", t("imp.uploading", { name: file.name })));
  const form = new FormData();
  form.append("file", file);
  try {
    state.preview = await api("/api/uploads", { method: "POST", form });
  } catch (err) { return showError(status, err); }
  clear(status, h("div", { class: "notice ok" }, t("imp.uploaded", { name: state.preview.original_filename, rows: fmtInt(state.preview.row_count), columns: state.preview.columns.length })));
  renderPreview();
  goto(2);
}

function renderPreview() {
  const p = state.preview;
  $("#file-info").textContent = t("imp.file_info", { name: p.original_filename, bytes: fmtInt(p.size_bytes), rows: fmtInt(p.row_count) });
  $("#encoding").value = p.encoding;
  $("#delimiter").value = p.delimiter === "\t" ? "\\t" : p.delimiter;
  clear($("#column-warnings"), p.column_warnings.map((w) => h("div", { class: "notice warn small" }, w)));
  const cols = p.columns.map((c) => ({ key: c, label: c, render: (r) => r[c] === "" ? h("span", { class: "muted" }, "∅") : r[c] }));
  clear($("#sample-table"), p.sample_rows.length ? table(cols, p.sample_rows.slice(0, 10)) : h("p", {}, t("imp.no_data_rows")));
  clear($("#mapping"), FIELDS.map(([key, label, req]) => h("label", {}, h("span", { class: req ? "req" : "" }, label),
    h("select", { "data-field": key, onchange: renderAttributes },
      h("option", { value: "" }, req ? t("imp.choose_column") : t("imp.not_mapped")),
      p.columns.map((c) => h("option", { value: c, selected: p.suggested_mapping[key] === c }, c))))));
  renderAttributes();
  // timestamp formats
  const fmt = $("#ts-format");
  const scores = Object.fromEntries((p.timestamp_format_candidates || []).map((c) => [c.format, c.parsed_share]));
  const formats = ["ISO8601", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "unix_s", "unix_ms", "custom"];
  clear(fmt, formats.map((f) => h("option", { value: f, selected: f === p.suggested_timestamp_format },
    f === "custom" ? t("imp.ts.custom_option") : `${f}${scores[f] !== undefined ? " — " + t("imp.ts.parses", { pct: (scores[f] * 100).toFixed(0) }) : ""}`)));
  $("#format-scores").textContent = p.timestamp_format_candidates?.length ? t("imp.ts.suggestion_note") : t("imp.ts.map_first");
}

function mapping() {
  const m = {};
  for (const sel of $$("#mapping select")) m[sel.dataset.field] = sel.value || null;
  m.attributes = $$("#attributes input:checked").map((i) => i.value);
  return m;
}

function renderAttributes() {
  const used = new Set($$("#mapping select").map((s) => s.value).filter(Boolean));
  const prev = new Set($$("#attributes input:checked").map((i) => i.value));
  const first = !$$("#attributes input").length;
  const avail = state.preview.columns.filter((c) => !used.has(c));
  clear($("#attributes"), avail.length ? avail.map((c) => h("label", { class: "chip" }, h("input", { type: "checkbox", value: c,
    checked: first ? state.preview.suggested_attributes.includes(c) : prev.has(c) }), h("span", {}, c)))
    : h("span", { class: "muted small" }, t("imp.all_mapped")));
}

function tsOptions() {
  const f = $("#ts-format").value;
  return {
    format: f === "custom" ? $("#ts-custom").value.trim() : f,
    source_timezone: $("#ts-tz").value,
    ambiguous_policy: $("#ts-ambiguous").value,
    nonexistent_policy: $("#ts-nonexistent").value,
  };
}

function importOptions() {
  return { mapping: mapping(), timestamps: tsOptions(), encoding: $("#encoding").value, delimiter: $("#delimiter").value };
}

async function validate() {
  const out = $("#validation");
  goto(4);
  clear(out, h("div", { class: "state" }, h("span", { class: "spinner" }), " ", t("imp.validating")));
  try {
    state.validation = await api(`/api/uploads/${state.preview.upload_id}/validate`, { method: "POST", body: importOptions() });
  } catch (err) {
    showError(out, err);
    return;
  }
  const v = state.validation;
  const base = `/api/uploads/${state.preview.upload_id}/validation-report`;
  $("#report-csv").href = `${base}?format=csv`;
  $("#report-json").href = `${base}?format=json`;
  const codes = Object.entries(v.counts_by_code).sort((a, b) => b[1] - a[1]);
  clear(out,
    h("div", { class: "kpis" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("imp.v.rows")), h("div", { class: "value" }, fmtInt(v.total_rows))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("imp.v.valid_rows")), h("div", { class: "value" }, fmtInt(v.valid_rows))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("imp.v.invalid_rows")), h("div", { class: "value" }, fmtInt(v.invalid_rows))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("imp.v.cases")), h("div", { class: "value" }, fmtInt(v.case_count))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, t("imp.v.errors_warnings")), h("div", { class: "value" }, `${fmtInt(v.error_count)} / ${fmtInt(v.warning_count)}`))),
    v.invalid_rows ? h("div", { class: "notice warn", style: { marginTop: "12px" } }, h("b", {}, t("imp.v.invalid_count", { n: fmtInt(v.invalid_rows) })), " ",
      t("imp.v.invalid_hint"))
      : h("div", { class: "notice ok", style: { marginTop: "12px" } }, t("imp.v.all_valid")),
    v.timestamp_tie_cases ? h("div", { class: "notice warn", style: { marginTop: "8px" } }, h("b", {}, t("imp.v.ties", { n: fmtInt(v.timestamp_tie_cases) })), " ",
      t("imp.v.ties_hint")) : null,
    h("p", { class: "small muted" }, t("imp.v.rework_kept")),
    codes.length ? h("div", { class: "chips" }, codes.map(([c, n]) => badge(`${c}: ${fmtInt(n)}`))) : null,
    v.findings.length ? h("div", { style: { marginTop: "10px" } }, findingsTable(v.findings)) : null,
    v.findings_truncated ? h("p", { class: "small muted" }, t("imp.v.truncated")) : null,
    v.preview_rows.length ? h("details", { style: { marginTop: "10px" } }, h("summary", {}, t("imp.v.normalized_preview")),
      table(Object.keys(v.preview_rows[0]).map((k) => ({ key: k, label: k, render: (r) => r[k] === null ? "—" : (typeof r[k] === "object" ? JSON.stringify(r[k]) : r[k]) })), v.preview_rows)) : null);
}

async function confirmImport() {
  const status = $("#import-status");
  const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const target = $("#target").value;
  const body = {
    ...importOptions(),
    invalid_row_policy: $('input[name="policy"]:checked').value,
    dataset_name: $("#ds-name").value.trim() || null,
    target_dataset_id: target ? Number(target) : null,
    display_timezone: $("#display-tz").value,
    completion: {
      mode: $("#completion-mode").value, end_activities: split($("#end-activities").value),
      completed_status_values: split($("#done-values").value), open_status_values: split($("#open-values").value),
    },
    reference_time: $("#ref-time").value ? `${$("#ref-time").value}Z` : null,
  };
  $("#confirm").disabled = true;
  try {
    const job = await api(`/api/uploads/${state.preview.upload_id}/import`, { method: "POST", body });
    const done = await followJob(job.id, status);
    if (done.status === "succeeded") {
      await refreshDatasets();
      refreshAlertBadge();
      clear(status, h("div", { class: "notice ok" }, h("b", {}, t("imp.done")), " ",
        t("imp.done_detail", { events: fmtInt(done.result.events), cases: fmtInt(done.result.cases), version: done.result.version }), " ",
        done.result.quarantined_rows ? t("imp.done_quarantined", { n: fmtInt(done.result.quarantined_rows) }) + " " : "",
        h("a", { href: `/?dataset=${done.dataset_id}` }, t("common.open_overview"))));
      toast(t("imp.done"));
    } else {
      clear(status, h("div", { class: "notice error" }, h("b", {}, t("imp.not_completed")), " ", done.error || done.status));
    }
  } catch (err) {
    showError(status, err);
  } finally {
    $("#confirm").disabled = false;
  }
}

async function initSelectors() {
  const { timezones } = await api("/api/meta/timezones");
  const guess = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  for (const id of ["#ts-tz", "#display-tz"]) {
    clear($(id), timezones.map((z) => h("option", { value: z, selected: z === "UTC" }, z)));
  }
  $("#ts-tz").title = t("imp.ts.browser_tz", { tz: guess });
  $("#ts-tz").addEventListener("change", () => { $("#display-tz").value = $("#ts-tz").value; });
  const tgt = $("#target");
  clear(tgt, h("option", { value: "" }, t("imp.new_dataset")), getDatasets().map((d) => h("option", { value: d.id }, t("imp.new_version_of", { name: d.name, version: d.version }))));
  const target = pageParam("target");
  if (target) tgt.value = target;
}

$("#file-input").addEventListener("change", (e) => upload(e.target.files[0]));
const dz = $("#dropzone");
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); upload(e.dataTransfer.files[0]); });
$("#reparse").addEventListener("click", async () => {
  try {
    const p = await api(`/api/uploads/${state.preview.upload_id}`, { params: { encoding: $("#encoding").value, delimiter: $("#delimiter").value } });
    state.preview = { ...p, encoding: $("#encoding").value, delimiter: $("#delimiter").value };
    renderPreview();
    toast(t("imp.reread_done"));
  } catch (err) { toast(err.message, "error"); }
});
$("#to-step-3").addEventListener("click", () => {
  const m = mapping();
  const missing = FIELDS.filter(([k, , req]) => req && !m[k]).map(([, l]) => l);
  if (missing.length) return toast(t("imp.map_required", { fields: missing.join(", ") }), "error");
  if (!$("#ds-name").value) $("#ds-name").value = state.preview.original_filename.replace(/\.[^.]+$/, "");
  goto(3);
});
$("#validate").addEventListener("click", validate);
$("#to-step-5").addEventListener("click", () => goto(5));
$("#confirm").addEventListener("click", confirmImport);
for (const b of $$("[data-back]")) b.addEventListener("click", () => goto(Number(b.dataset.back)));
$("#ts-format").addEventListener("change", () => { $("#ts-custom").disabled = $("#ts-format").value !== "custom"; });

bootPage(async () => {}, { filters: false }).then(() => { initSelectors(); goto(1); $("#ts-custom").disabled = true; });
