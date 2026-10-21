import {
  $, api, badge, bootPage, clear, currentDatasetId, fmtInt, fmtTs, getFilterOptions, h, loading, pageParam,
  refreshAlertBadge, refreshDatasets, reveal, setUrl, showError, t, table, toast,
} from "./core.js";
import { findingsTable } from "./findings.js";
import { followJob } from "./jobs.js";

const root = $("#datasets-root");
let timezones = null;

async function loadDemo() {
  const btn = $("#load-demo");
  btn.disabled = true;
  const status = $("#demo-status");
  try {
    const job = await api("/api/demo/load", { method: "POST" });
    const done = await followJob(job.id, status);
    if (done.status === "succeeded") {
      const a = done.result.alerts || {};
      clear(status, h("div", { class: "notice ok" }, h("b", {}, t("ds.demo_loaded")), " ",
        t("ds.demo_loaded_detail", { cases: fmtInt(done.result.cases), events: fmtInt(done.result.events), alerts: fmtInt(a.created || 0) }), " ",
        h("a", { href: `/?dataset=${done.dataset_id}` }, t("common.open_overview"))));
      await refreshDatasets();
      setUrl({ dataset: done.dataset_id, filters: {} }, { replace: true });
      refreshAlertBadge();
      render();
    } else {
      clear(status, h("div", { class: "notice error" }, h("b", {}, t("ds.demo_failed")), " ", done.error || done.status));
    }
  } catch (err) {
    showError(status, err);
  } finally {
    btn.disabled = false;
  }
}

function openDelete(ds) {
  const dlg = $("#delete-dialog");
  $("#delete-name").textContent = ds.name;
  const input = $("#delete-confirm");
  input.value = "";
  $("#delete-submit").disabled = true;
  input.oninput = () => { $("#delete-submit").disabled = input.value !== ds.name; };
  $("#delete-cancel").onclick = () => dlg.close();
  $("#delete-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api(`/api/datasets/${ds.id}`, { method: "DELETE", params: { confirm_name: input.value } });
      dlg.close();
      toast(t("ds.deleted", { name: ds.name }));
      await refreshDatasets();
      setUrl({ filters: {} }, { replace: true });
      $("#settings-card").classList.add("hidden");
      $("#report-card").classList.add("hidden");
      refreshAlertBadge();
      render();
    } catch (err) { toast(err.message, "error"); }
  };
  dlg.showModal();
  input.focus();
}

async function showReport(ds) {
  const card = $("#report-card");
  card.classList.remove("hidden");
  loading(card, t("ds.loading_report"));
  try {
    const rep = await api(`/api/datasets/${ds.id}/validation-report`, { params: { limit: 200 } });
    const codes = Object.entries(rep.counts_by_code).sort((a, b) => b[1] - a[1]);
    clear(card,
      h("div", { class: "card-head" }, h("h2", {}, t("ds.report.title", { name: ds.name, version: rep.version })),
        h("div", { class: "actions" },
          h("a", { class: "btn btn-sm", href: `/api/datasets/${ds.id}/validation-report?format=csv`, download: "" }, t("ds.report.csv")),
          rep.quarantined_count ? h("a", { class: "btn btn-sm", href: `/api/datasets/${ds.id}/quarantine?format=csv`, download: "" }, t("ds.report.quarantine_csv")) : null)),
      h("p", { class: "small muted" }, t("ds.report.summary", { rows: fmtInt(rep.source_row_count), events: fmtInt(rep.event_count), quarantined: fmtInt(rep.quarantined_count) })),
      codes.length ? h("div", { class: "chips" }, codes.map(([c, n]) => badge(`${c}: ${fmtInt(n)}`))) : h("p", {}, t("ds.report.none")),
      rep.findings.length ? findingsTable(rep.findings) : null);
    reveal(card);
  } catch (err) { showError(card, err); }
}

async function showSettings(ds) {
  const card = $("#settings-card");
  card.classList.remove("hidden");
  loading(card);
  if (!timezones) timezones = (await api("/api/meta/timezones")).timezones;
  let opts = getFilterOptions();
  if (!opts || currentDatasetId() !== ds.id) opts = await api(`/api/datasets/${ds.id}/filter-options`);
  const tz = h("select", { name: "display_timezone" }, timezones.map((z) => h("option", { value: z, selected: z === ds.display_timezone }, z)));
  const mode = h("select", { name: "completion_mode" },
    [["none", t("completion.none")], ["end_activity", t("completion.end_activity")], ["case_status", t("completion.case_status")]]
      .map(([v, l]) => h("option", { value: v, selected: v === ds.completion_mode }, l)));
  const ends = h("select", { name: "completion_activities", multiple: true },
    opts.activities.map((a) => h("option", { value: a, selected: ds.completion_activities.includes(a) }, a)));
  const done = h("input", { name: "completed_status_values", value: ds.completed_status_values.join(", "), placeholder: t("completion.done_placeholder") });
  const open = h("input", { name: "open_status_values", value: ds.open_status_values.join(", "), placeholder: t("completion.open_placeholder") });
  const ref = h("input", { type: "datetime-local", name: "reference_time", step: "1",
    value: ds.reference_time ? ds.reference_time.slice(0, 19) : "" });
  const name = h("input", { name: "name", value: ds.name, maxlength: "200" });
  const form = h("form", { class: "form-grid", onsubmit: async (e) => {
    e.preventDefault();
    const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const body = {
      name: name.value, display_timezone: tz.value, completion_mode: mode.value,
      completion_activities: [...ends.selectedOptions].map((o) => o.value),
      completed_status_values: split(done.value), open_status_values: split(open.value),
    };
    if (ref.value) body.reference_time = `${ref.value}Z`; else body.clear_reference_time = true;
    try {
      await api(`/api/datasets/${ds.id}`, { method: "PATCH", body });
      toast(t("ds.settings.saved"));
      await refreshDatasets();
      render();
      card.classList.add("hidden");
    } catch (err) { toast(err.message, "error"); }
  } },
  h("label", {}, t("ds.settings.name"), name),
  h("label", {}, t("ds.settings.display_tz"), tz),
  h("label", {}, t("ds.settings.completion_rule"), mode),
  h("label", {}, t("ds.settings.end_activities"), ends),
  h("label", {}, t("ds.settings.completed_values"), done),
  h("label", {}, t("ds.settings.open_values"), open),
  h("label", {}, t("ds.settings.reference_time"), ref),
  h("div", { class: "actions", style: { alignSelf: "end" } }, h("button", { class: "btn btn-primary", type: "submit" }, t("common.save")),
    h("button", { class: "btn", type: "button", onclick: () => card.classList.add("hidden") }, t("common.close"))));
  clear(card, h("div", { class: "card-head" }, h("h2", {}, t("ds.settings.title", { name: ds.name }))),
    h("p", { class: "small muted" }, t("ds.settings.completion_note")),
    form);
  reveal(card);
}

async function render() {
  let list;
  try { list = await api("/api/datasets"); } catch (err) { return showError(root, err); }
  if (!list.length) {
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, t("ds.none.title")), t("ds.none.hint")));
  }
  const cur = currentDatasetId();
  clear(root, table([
    { key: "name", label: t("ds.col.dataset"), render: (d) => h("div", {}, h("b", {}, d.name), " ", d.is_demo ? badge(t("ds.synthetic"), "synthetic") : null,
      h("div", { class: "small muted" }, `${d.source_filename} · sha256 ${d.source_sha256.slice(0, 12)}…`)) },
    { key: "version", label: t("ds.col.version"), num: true, render: (d) => `v${d.version}` },
    { key: "case_count", label: t("common.cases"), num: true, render: (d) => fmtInt(d.case_count) },
    { key: "event_count", label: t("common.events"), num: true, render: (d) => fmtInt(d.event_count) },
    { key: "quarantined_count", label: t("ds.col.quarantined"), num: true, render: (d) => d.quarantined_count ? badge(fmtInt(d.quarantined_count), "warn") : "0" },
    { key: "completion", label: t("ds.col.completion"), render: (d) => t(`completion.short.${d.completion_mode}`) },
    { key: "imported_at", label: t("ds.col.imported"), render: (d) => fmtTs(`${d.imported_at}Z`, "UTC") },
    { key: "actions", label: "", render: (d) => h("div", { class: "actions" },
      h("a", { class: "btn btn-sm", href: `/?dataset=${d.id}` }, t("common.open")),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => showSettings(d) }, t("ds.action.settings")),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => showReport(d) }, t("ds.action.findings")),
      h("a", { class: "btn btn-sm", href: `/import?target=${d.id}` }, t("ds.action.reimport")),
      h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: () => openDelete(d) }, t("common.delete"))) },
  ], list, { rowClass: (d) => (d.id === cur ? "selected" : "") }));
  reveal(root.querySelector("tbody"), { stagger: 30 });
}

$("#load-demo").addEventListener("click", loadDemo);
bootPage(render, { filters: false }).then(() => {
  if (pageParam("demo") === "1") {
    history.replaceState(null, "", location.pathname);
    loadDemo();
  }
});
