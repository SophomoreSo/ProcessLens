import {
  $, api, badge, bootPage, clear, currentDatasetId, fmtInt, fmtTs, getFilterOptions, h, loading, pageParam,
  refreshAlertBadge, refreshDatasets, setUrl, showError, table, toast,
} from "./core.js";
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
      clear(status, h("div", { class: "notice ok" }, h("b", {}, "Demo data loaded. "),
        `${fmtInt(done.result.cases)} synthetic cases, ${fmtInt(done.result.events)} events. `,
        `Example rules were added and ${fmtInt(a.created || 0)} alerts were created by the post-import evaluation. `,
        h("a", { href: `/?dataset=${done.dataset_id}` }, "Open the overview →")));
      await refreshDatasets();
      setUrl({ dataset: done.dataset_id, filters: {} }, { replace: true });
      refreshAlertBadge();
      render();
    } else {
      clear(status, h("div", { class: "notice error" }, h("b", {}, "Demo import failed: "), done.error || done.status));
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
      toast(`Deleted dataset “${ds.name}”.`);
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
  loading(card, "Loading validation report…");
  try {
    const rep = await api(`/api/datasets/${ds.id}/validation-report`, { params: { limit: 200 } });
    const codes = Object.entries(rep.counts_by_code).sort((a, b) => b[1] - a[1]);
    clear(card,
      h("div", { class: "card-head" }, h("h2", {}, `Import findings — ${ds.name} (v${rep.version})`),
        h("div", { class: "actions" },
          h("a", { class: "btn btn-sm", href: `/api/datasets/${ds.id}/validation-report?format=csv`, download: "" }, "Full report CSV"),
          rep.quarantined_count ? h("a", { class: "btn btn-sm", href: `/api/datasets/${ds.id}/quarantine?format=csv`, download: "" }, "Quarantined rows CSV") : null)),
      h("p", { class: "small muted" }, `${fmtInt(rep.source_row_count)} source rows → ${fmtInt(rep.event_count)} events imported, ${fmtInt(rep.quarantined_count)} rows quarantined.`),
      codes.length ? h("div", { class: "chips" }, codes.map(([c, n]) => badge(`${c}: ${fmtInt(n)}`))) : h("p", {}, "No findings."),
      rep.findings.length ? table([
        { key: "severity", label: "Severity", render: (f) => badge(f.severity, f.severity) },
        { key: "code", label: "Code", render: (f) => h("code", {}, f.code) },
        { key: "row", label: "Row", num: true, render: (f) => f.row ?? "—" },
        { key: "field", label: "Field", render: (f) => f.field ?? "—" },
        { key: "case_id", label: "Case", render: (f) => f.case_id ?? "—" },
        { key: "value", label: "Value", render: (f) => f.value ?? "—" },
        { key: "message", label: "Message" },
      ], rep.findings) : null);
  } catch (err) { showError(card, err); }
}

async function showSettings(ds) {
  const card = $("#settings-card");
  card.classList.remove("hidden");
  loading(card, "Loading settings…");
  if (!timezones) timezones = (await api("/api/meta/timezones")).timezones;
  let opts = getFilterOptions();
  if (!opts || currentDatasetId() !== ds.id) opts = await api(`/api/datasets/${ds.id}/filter-options`);
  const tz = h("select", { name: "display_timezone" }, timezones.map((t) => h("option", { value: t, selected: t === ds.display_timezone }, t)));
  const mode = h("select", { name: "completion_mode" },
    [["none", "No completion rule (all cases 'unknown')"], ["end_activity", "Completed when the last event is an end activity"],
      ["case_status", "Use the mapped case-status column"]].map(([v, l]) => h("option", { value: v, selected: v === ds.completion_mode }, l)));
  const ends = h("select", { name: "completion_activities", multiple: true },
    opts.activities.map((a) => h("option", { value: a, selected: ds.completion_activities.includes(a) }, a)));
  const done = h("input", { name: "completed_status_values", value: ds.completed_status_values.join(", "), placeholder: "e.g. Completed, Closed" });
  const open = h("input", { name: "open_status_values", value: ds.open_status_values.join(", "), placeholder: "e.g. Open, In progress" });
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
      toast("Settings saved. Analyses now use the new settings.");
      await refreshDatasets();
      render();
      card.classList.add("hidden");
    } catch (err) { toast(err.message, "error"); }
  } },
  h("label", {}, "Name", name),
  h("label", {}, "Display timezone", tz),
  h("label", {}, "Completion rule", mode),
  h("label", {}, "End activities (for 'end activity' rule)", ends),
  h("label", {}, "Completed status values", done),
  h("label", {}, "Open status values (others → unknown)", open),
  h("label", {}, "Reference time for open-case age (UTC; empty = latest event)", ref),
  h("div", { class: "actions", style: { alignSelf: "end" } }, h("button", { class: "btn btn-primary", type: "submit" }, "Save settings"),
    h("button", { class: "btn", type: "button", onclick: () => card.classList.add("hidden") }, "Close")));
  clear(card, h("div", { class: "card-head" }, h("h2", {}, `Analysis settings — ${ds.name}`)),
    h("p", { class: "small muted" }, "A case is never treated as complete just because no later event was observed. Choose an explicit completion rule."),
    form);
}

async function render() {
  let list;
  try { list = await api("/api/datasets"); } catch (err) { return showError(root, err); }
  if (!list.length) {
    return clear(root, h("div", { class: "state" }, h("div", { class: "title" }, "No datasets yet"),
      "Load the synthetic demo data or import your own CSV event log."));
  }
  const cur = currentDatasetId();
  clear(root, table([
    { key: "name", label: "Dataset", render: (d) => h("div", {}, h("b", {}, d.name), " ", d.is_demo ? badge("synthetic", "synthetic") : null,
      h("div", { class: "small muted" }, `${d.source_filename} · sha256 ${d.source_sha256.slice(0, 12)}…`)) },
    { key: "version", label: "Version", num: true, render: (d) => `v${d.version}` },
    { key: "case_count", label: "Cases", num: true, render: (d) => fmtInt(d.case_count) },
    { key: "event_count", label: "Events", num: true, render: (d) => fmtInt(d.event_count) },
    { key: "quarantined_count", label: "Quarantined", num: true, render: (d) => d.quarantined_count ? badge(fmtInt(d.quarantined_count), "warn") : "0" },
    { key: "completion", label: "Completion rule", render: (d) => d.completion_mode },
    { key: "imported_at", label: "Imported (UTC)", render: (d) => fmtTs(`${d.imported_at}Z`, "UTC") },
    { key: "actions", label: "", render: (d) => h("div", { class: "actions" },
      h("a", { class: "btn btn-sm", href: `/?dataset=${d.id}` }, "Open"),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => showSettings(d) }, "Settings"),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => showReport(d) }, "Findings"),
      h("a", { class: "btn btn-sm", href: `/import?target=${d.id}` }, "Re-import"),
      h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: () => openDelete(d) }, "Delete")) },
  ], list, { rowClass: (d) => (d.id === cur ? "selected" : "") }));
}

$("#load-demo").addEventListener("click", loadDemo);
bootPage(render, { filters: false }).then(() => {
  if (pageParam("demo") === "1") {
    history.replaceState(null, "", location.pathname);
    loadDemo();
  }
});
