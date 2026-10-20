// ProcessLens shared frontend utilities.
// All business calculations happen in the Python backend; this module only
// fetches, formats and renders. DOM is built with textContent (never innerHTML
// with data) to avoid HTML injection.

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function api(path, { method = "GET", body, form, params } = {}) {
  let url = path;
  if (params) {
    const qs = params instanceof URLSearchParams ? params.toString() : new URLSearchParams(params).toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }
  const headers = { Accept: "application/json" };
  const opts = { method, headers, credentials: "same-origin" };
  if (method !== "GET") headers["X-CSRF-Token"] = CSRF;
  if (form) opts.body = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError(0, "network_error", "Cannot reach the ProcessLens server. Is it still running?");
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(res.status, err.code || "http_error", err.message || `Request failed (${res.status})`, err.details);
  }
  return data;
}

// ---------------------------------------------------------------- DOM helper
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  appendChildren(el, children);
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------- formatting
const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

export const fmtInt = (v) => (v === null || v === undefined ? "—" : nf.format(v));
export const fmtNum = (v, d = 1) => (v === null || v === undefined ? "—" : (d === 2 ? nf2 : nf1).format(v));
export const fmtPct = (v, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);

export function fmtDur(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.abs(seconds);
  const sign = seconds < 0 ? "−" : "";
  if (s < 60) return `${sign}${nf.format(s)} s`;
  if (s < 3600) return `${sign}${nf1.format(s / 60)} min`;
  if (s < 86400 * 2) return `${sign}${nf1.format(s / 3600)} h`;
  return `${sign}${nf1.format(s / 86400)} d`;
}

export function fmtTs(iso, tz, { seconds = false, dateOnly = false } = {}) {
  if (!iso) return "—";
  const d = new Date(iso);
  const opts = dateOnly
    ? { year: "numeric", month: "short", day: "2-digit", timeZone: tz || "UTC" }
    : { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
        ...(seconds ? { second: "2-digit" } : {}), hour12: false, timeZone: tz || "UTC" };
  try {
    return new Intl.DateTimeFormat(undefined, opts).format(d);
  } catch {
    return iso;
  }
}

export const STATUS_LABEL = { completed: "completed", incomplete: "incomplete", unknown: "unknown status" };
export const RESULT_LABEL = { compliant: "compliant", violating: "violating", not_evaluable: "not evaluable" };

export function badge(text, cls = "") {
  return h("span", { class: `badge ${cls}` }, text);
}

// ---------------------------------------------------------------- UI states
export function loading(el, text = "Loading…") {
  return clear(el, h("div", { class: "state" }, h("span", { class: "spinner" }), " ", text));
}

export function empty(el, title, hint, action) {
  return clear(el, h("div", { class: "state" }, h("div", { class: "title" }, title), hint ? h("div", {}, hint) : null,
    action ? h("div", { style: { marginTop: "10px" } }, action) : null));
}

export function showError(el, err) {
  const msg = err instanceof ApiError ? err.message : String(err?.message || err);
  let hint = "";
  if (err?.code === "network_error") hint = "Start the server again (see README) and reload this page.";
  else if (err?.code === "csrf_failed") hint = "Reload the page to refresh the security token.";
  else if (err?.status === 404) hint = "The item may have been deleted. Choose another dataset.";
  else if (err?.code === "invalid_filter") hint = "Adjust or clear the filters.";
  else if (err?.status >= 500) hint = "Check the server log for details.";
  return clear(el, h("div", { class: "notice error", role: "alert" }, h("b", {}, "Something went wrong: "), msg,
    hint ? h("div", { class: "small" }, hint) : null));
}

export function toast(message, kind = "") {
  const t = h("div", { class: `toast ${kind}`, role: "status" }, message);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}

// ---------------------------------------------------------------- URL state
const LIST_KEYS = ["activity", "exclude_activity", "resource", "department", "variant", "status", "conformance_result"];
const SCALAR_KEYS = ["start_from", "start_to", "min_duration_h", "max_duration_h", "rework", "conformance_rule",
  "transition_from", "transition_to"];
export const FILTER_KEYS = [...LIST_KEYS, ...SCALAR_KEYS];

export function readFilters(search = location.search) {
  const p = new URLSearchParams(search);
  const f = {};
  for (const k of LIST_KEYS) { const v = p.getAll(k).filter(Boolean); if (v.length) f[k] = v; }
  for (const k of SCALAR_KEYS) { const v = p.get(k); if (v !== null && v !== "") f[k] = v; }
  return f;
}

export function filterParams(filters = readFilters()) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else if (v !== undefined && v !== null && v !== "") p.set(k, v);
  }
  return p;
}

export function currentDatasetId() {
  const p = new URLSearchParams(location.search);
  const v = p.get("dataset") || safeStorage("get", "processlens.dataset");
  return v ? Number(v) : null;
}

function safeStorage(op, key, value) {
  try {
    if (op === "get") return localStorage.getItem(key);
    if (op === "set") localStorage.setItem(key, value);
    if (op === "del") localStorage.removeItem(key);
  } catch { /* storage unavailable */ }
  return null;
}

export function setUrl({ dataset, filters, extra } = {}, { replace = false } = {}) {
  const p = filters !== undefined ? filterParams(filters) : filterParams(readFilters());
  const ds = dataset !== undefined ? dataset : currentDatasetId();
  if (ds) p.set("dataset", ds);
  const cur = new URLSearchParams(location.search);
  for (const [k, v] of cur.entries()) {
    if (!FILTER_KEYS.includes(k) && k !== "dataset" && !(extra && k in extra)) p.append(k, v);
  }
  if (extra) for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== "") p.set(k, v);
  const url = `${location.pathname}?${p.toString()}`;
  if (replace) history.replaceState(null, "", url); else history.pushState(null, "", url);
  if (ds) safeStorage("set", "processlens.dataset", ds);
  syncNavLinks();
}

export function pageParam(name) {
  return new URLSearchParams(location.search).get(name);
}

export function linkWith(path, { filters, extra } = {}) {
  const p = filterParams(filters !== undefined ? filters : readFilters());
  const ds = currentDatasetId();
  if (ds) p.set("dataset", ds);
  if (extra) for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined) p.set(k, v);
  return `${path}?${p.toString()}`;
}

function syncNavLinks() {
  for (const a of $$("a[data-keep-query]")) {
    const path = new URL(a.href, location.origin).pathname;
    a.href = linkWith(path);
  }
}

// ---------------------------------------------------------------- datasets & filters bar
let datasetsCache = null;
let filterOptions = null;
let onChange = () => {};

export function getDatasets() { return datasetsCache || []; }
export function currentDataset() { return getDatasets().find((d) => d.id === currentDatasetId()) || null; }
export function getFilterOptions() { return filterOptions; }

async function loadFilterOptions() {
  filterOptions = null;
  const ds = currentDatasetId();
  if (!ds) return null;
  try { filterOptions = await api(`/api/datasets/${ds}/filter-options`); } catch { filterOptions = null; }
  return filterOptions;
}

const CHIP_LABELS = {
  start_from: "Started from", start_to: "Started until", activity: "Has activity", exclude_activity: "Lacks activity",
  resource: "Resource", department: "Department", variant: "Variant", min_duration_h: "Duration ≥ (h)",
  max_duration_h: "Duration ≤ (h)", rework: "Rework", status: "Status", conformance_result: "Conformance",
};

function renderChips() {
  const box = $("#filter-chips");
  if (!box) return;
  const f = readFilters();
  const chips = [];
  const remove = (key, value) => () => {
    const nf = readFilters();
    if (key === "transition") { delete nf.transition_from; delete nf.transition_to; }
    else if (key === "conformance") { delete nf.conformance_rule; delete nf.conformance_result; }
    else if (Array.isArray(nf[key])) { nf[key] = nf[key].filter((x) => x !== value); if (!nf[key].length) delete nf[key]; }
    else delete nf[key];
    applyFilters(nf);
  };
  const chip = (label, key, value) => chips.push(h("span", { class: "chip" }, h("span", { title: `${label}: ${value}` }, `${label}: ${value}`),
    h("button", { type: "button", "aria-label": `Remove filter ${label} ${value}`, onclick: remove(key, value) }, "×")));
  for (const [k, v] of Object.entries(f)) {
    if (["transition_from", "transition_to", "conformance_rule", "conformance_result"].includes(k)) continue;
    if (Array.isArray(v)) v.forEach((x) => chip(CHIP_LABELS[k] || k, k, k === "variant" ? `#${x.slice(0, 8)}` : x));
    else chip(CHIP_LABELS[k] || k, k, k === "rework" ? (v === "true" ? "yes" : "no") : v);
  }
  if (f.transition_from) {
    const name = (s) => (s === "__start__" ? "▶ start" : s === "__end__" ? "■ end" : s);
    chip("Transition", "transition", `${name(f.transition_from)} → ${name(f.transition_to)}`);
  }
  if (f.conformance_rule && f.conformance_result) {
    const rule = filterOptions?.conformance_rules?.find((r) => String(r.id) === String(f.conformance_rule));
    chip("Conformance", "conformance", `${rule ? rule.name : "rule " + f.conformance_rule}: ${f.conformance_result.map((r) => RESULT_LABEL[r] || r).join(", ")}`);
  }
  if (chips.length) {
    chips.push(h("button", { type: "button", class: "btn-ghost btn-sm", onclick: () => applyFilters({}) }, "Clear all"));
  } else {
    chips.push(h("span", { class: "muted small" }, "No filters — all cases of the dataset are included."));
  }
  clear(box, chips);
  const count = Object.keys(f).filter((k) => !["transition_to", "conformance_result"].includes(k)).length;
  const fc = $("#filter-count");
  if (fc) fc.textContent = String(count);
}

export function applyFilters(filters) {
  setUrl({ filters });
  renderChips();
  onChange();
}

function fillMulti(select, values, selected) {
  clear(select, values.map((v) => h("option", { value: v, selected: selected.includes(v) }, v)));
}

function openFilterDialog() {
  const dlg = $("#filter-dialog");
  const form = $("#filter-form");
  const f = readFilters();
  const o = filterOptions || { activities: [], resources: [], departments: [], conformance_rules: [] };
  for (const sel of $$("select[data-options]", form)) {
    const key = sel.dataset.options;
    if (key === "conformance_rules") {
      clear(sel, h("option", { value: "" }, "—"),
        (o.conformance_rules || []).map((r) => h("option", { value: r.id, selected: String(r.id) === String(f.conformance_rule) }, r.name)));
    } else {
      fillMulti(sel, o[key] || [], f[sel.name] || []);
      sel.disabled = !(o[key] || []).length;
    }
  }
  for (const name of ["start_from", "start_to", "min_duration_h", "max_duration_h"]) form.elements[name].value = f[name] || "";
  if (o.case_start_range) {
    form.elements.start_from.min = form.elements.start_to.min = o.case_start_range.min || "";
    form.elements.start_from.max = form.elements.start_to.max = o.case_start_range.max || "";
  }
  form.elements.rework.value = f.rework || "";
  for (const cb of $$('input[type="checkbox"]', form)) cb.checked = (f[cb.name] || []).includes(cb.value);
  dlg.showModal();
}

function collectFilterForm() {
  const form = $("#filter-form");
  const cur = readFilters();
  const f = {};
  for (const sel of $$("select[multiple]", form)) {
    const v = [...sel.selectedOptions].map((o) => o.value);
    if (v.length) f[sel.name] = v;
  }
  for (const name of ["start_from", "start_to", "min_duration_h", "max_duration_h", "rework", "conformance_rule"]) {
    const v = form.elements[name].value;
    if (v !== "") f[name] = v;
  }
  for (const name of ["status", "conformance_result"]) {
    const v = $$(`input[name="${name}"]:checked`, form).map((c) => c.value);
    if (v.length) f[name] = v;
  }
  if (f.conformance_rule && !f.conformance_result) delete f.conformance_rule;
  if (f.conformance_result && !f.conformance_rule) delete f.conformance_result;
  for (const k of ["variant", "transition_from", "transition_to"]) if (cur[k]) f[k] = cur[k];
  return f;
}

function renderDatasetBadges() {
  const box = $("#dataset-badges");
  if (!box) return;
  const ds = currentDataset();
  if (!ds) return clear(box);
  clear(box,
    ds.is_demo ? h("span", { class: "badge synthetic", title: "Generated demo data, not real business data" }, "SYNTHETIC DEMO DATA") : null,
    h("span", { class: "badge", title: `Dataset version ${ds.version}` }, `v${ds.version}`),
    h("span", { class: "muted small" }, `${fmtInt(ds.case_count)} cases · ${fmtInt(ds.event_count)} events · times in ${ds.display_timezone}`),
    ds.quarantined_count ? h("span", { class: "badge warn", title: "Invalid rows kept aside during import" }, `${fmtInt(ds.quarantined_count)} quarantined rows`) : null);
}

async function refreshAlertBadge() {
  const badgeEl = $("#nav-alert-badge");
  if (!badgeEl) return;
  try {
    const ds = currentDatasetId();
    const c = await api("/api/alerts/counts", { params: ds ? { dataset_id: ds } : {} });
    badgeEl.textContent = String(c.open);
    badgeEl.classList.toggle("hidden", !c.open);
  } catch { badgeEl.classList.add("hidden"); }
}

export async function refreshDatasets() {
  datasetsCache = await api("/api/datasets");
  const sel = $("#dataset-select");
  let id = currentDatasetId();
  if (id && !datasetsCache.some((d) => d.id === id)) id = null;
  if (!id && datasetsCache.length) id = datasetsCache[0].id;
  if (sel) {
    clear(sel, datasetsCache.length
      ? datasetsCache.map((d) => h("option", { value: d.id, selected: d.id === id }, `${d.name} (v${d.version})`))
      : h("option", { value: "" }, "No datasets yet"));
    sel.disabled = !datasetsCache.length;
  }
  if (id !== currentDatasetId()) setUrl({ dataset: id }, { replace: true });
  else if (!id) safeStorage("del", "processlens.dataset");
  renderDatasetBadges();
  return datasetsCache;
}

export function noDatasetState(el) {
  return empty(el, "No dataset loaded yet",
    "Import a CSV event log or load the synthetic purchase-to-pay demo to get started.",
    h("div", { class: "actions", style: { justifyContent: "center" } },
      h("a", { class: "btn btn-primary", href: "/datasets?demo=1" }, "Load demo data"),
      h("a", { class: "btn", href: "/import" }, "Import a CSV")));
}

function initTheme() {
  const saved = safeStorage("get", "processlens.theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle")?.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    safeStorage("set", "processlens.theme", next);
    window.dispatchEvent(new Event("themechange"));
  });
}

/**
 * Boot a page: loads datasets, wires the dataset selector and filter bar,
 * then calls ``render()`` now and whenever dataset or filters change.
 */
export async function bootPage(render, { filters = true } = {}) {
  initTheme();
  onChange = async () => { await loadFilterOptions(); renderChips(); render(); };
  try {
    await refreshDatasets();
  } catch (err) {
    showError($("#content"), err);
    return;
  }
  syncNavLinks();
  $("#dataset-select")?.addEventListener("change", (e) => {
    setUrl({ dataset: Number(e.target.value), filters: {} });
    renderDatasetBadges();
    refreshAlertBadge();
    onChange();
  });
  if (filters) {
    $("#filters-button")?.addEventListener("click", openFilterDialog);
    $("#filter-form")?.addEventListener("submit", (e) => { e.preventDefault(); $("#filter-dialog").close(); applyFilters(collectFilterForm()); });
    $("#filter-clear")?.addEventListener("click", () => { $("#filter-dialog").close(); applyFilters({}); });
    for (const b of $$("#filter-dialog [data-close]")) b.addEventListener("click", () => $("#filter-dialog").close());
  } else {
    $("#filters-button")?.classList.add("hidden");
  }
  window.addEventListener("popstate", () => { renderChips(); renderDatasetBadges(); onChange(); });
  await loadFilterOptions();
  renderChips();
  refreshAlertBadge();
  render();
}

export { refreshAlertBadge };

// ---------------------------------------------------------------- tables
/**
 * Render a table. columns: [{key, label, num, sortable, render(row) -> Node|string}]
 */
export function table(columns, rows, { onRowClick, sort, onSort, rowClass } = {}) {
  const thead = h("thead", {}, h("tr", {}, columns.map((c) => {
    const attrs = { class: [c.num ? "num" : "", c.sortable ? "sortable" : ""].join(" "), scope: "col" };
    if (c.sortable && sort && sort.key === c.key) attrs["aria-sort"] = sort.order === "desc" ? "descending" : "ascending";
    if (c.sortable && onSort) {
      attrs.tabindex = "0";
      attrs.onclick = () => onSort(c.key);
      attrs.onkeydown = (e) => { if (e.key === "Enter") onSort(c.key); };
    }
    return h("th", attrs, c.label);
  })));
  const tbody = h("tbody", {}, rows.map((r) => {
    const tr = h("tr", { class: [onRowClick ? "clickable" : "", rowClass ? rowClass(r) : ""].join(" ") },
      columns.map((c) => h("td", { class: c.num ? "num" : "" }, c.render ? c.render(r) : (r[c.key] ?? "—"))));
    if (onRowClick) {
      tr.tabIndex = 0;
      tr.addEventListener("click", () => onRowClick(r, tr));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") onRowClick(r, tr); });
    }
    return tr;
  }));
  return h("div", { class: "table-wrap" }, h("table", {}, thead, tbody));
}

export function pager({ page, pageSize, total }, onPage) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return h("div", { class: "pager" },
    h("span", { class: "muted" }, `${fmtInt(total)} rows · page ${page} of ${pages}`),
    h("button", { type: "button", class: "btn-sm", disabled: page <= 1, onclick: () => onPage(page - 1) }, "‹ Prev"),
    h("button", { type: "button", class: "btn-sm", disabled: page >= pages, onclick: () => onPage(page + 1) }, "Next ›"));
}

export function variantSequence(activities) {
  const seen = new Set();
  const parts = [];
  activities.forEach((a, i) => {
    if (i) parts.push(h("span", { class: "arrow", "aria-hidden": "true" }, "→"));
    parts.push(h("span", { class: `step ${seen.has(a) ? "repeat" : ""}`, title: seen.has(a) ? "Repeated activity" : null }, a));
    seen.add(a);
  });
  return h("div", { class: "seq", "aria-label": activities.join(" then ") }, parts);
}

export function exportLinks(datasetId, kinds, extra = {}) {
  const p = filterParams();
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  const qs = p.toString() ? `?${p.toString()}` : "";
  return h("div", { class: "actions" }, h("span", { class: "muted small" }, "Export filtered:"),
    kinds.map(([kind, fmt, label]) => h("a", { class: "btn btn-sm", href: `/api/datasets/${datasetId}/exports/${kind}.${fmt}${qs}`,
      download: "" }, label)));
}
