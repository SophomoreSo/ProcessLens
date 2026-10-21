// ProcessLens shared frontend utilities.
// All business calculations happen in the Python backend; this module only
// fetches, formats and renders. DOM is built with textContent (never innerHTML
// with data) to avoid HTML injection.

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

// ---------------------------------------------------------------- i18n
// The server embeds the catalog of the selected language (English merged in as fallback).
const CATALOG = (() => {
  try { return JSON.parse(document.getElementById("i18n-catalog")?.textContent || "{}"); } catch { return {}; }
})();
export const LANG = document.documentElement.lang || "en";
export const LOCALE = document.documentElement.dataset.locale || "en-US";

/** Translate ``key``, filling ``{name}`` placeholders from ``params``. Unknown keys render as the key. */
export function t(key, params = {}) {
  const template = CATALOG[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

// ---------------------------------------------------------------- motion
const reducedMotionQuery = matchMedia("(prefers-reduced-motion: reduce)");

/** Animations are on when the user setting says so ("system" follows the OS reduce-motion setting). */
export function motionEnabled() {
  const pref = document.documentElement.dataset.motion || "system";
  if (pref === "on") return true;
  if (pref === "off") return false;
  return !reducedMotionQuery.matches;
}

function applyMotionState() {
  document.documentElement.dataset.motionActive = String(motionEnabled());
}
applyMotionState();
reducedMotionQuery.addEventListener("change", () => { applyMotionState(); window.dispatchEvent(new Event("motionchange")); });

/** Staggered entrance animation for the direct children of ``el`` (no-op when motion is off). */
export function reveal(el, { stagger = 45, max = 12 } = {}) {
  if (!el || !motionEnabled()) return el;
  [...el.children].forEach((child, i) => {
    child.classList.remove("anim-in");
    child.style.setProperty("--i", String(Math.min(i, max)));
    void child.offsetWidth; // restart the animation
    child.classList.add("anim-in");
  });
  el.dataset.stagger = String(stagger);
  return el;
}

/** Animate a number from 0 to ``value`` inside ``el`` using ``format`` (renders the final value when motion is off). */
export function countUp(el, value, format, duration = 750) {
  if (value === null || value === undefined || Number.isNaN(value) || !motionEnabled()) {
    el.textContent = format(value);
    return;
  }
  const start = performance.now();
  const ease = (x) => 1 - (1 - x) ** 3;
  const step = (now) => {
    const p = Math.min(1, (now - start) / duration);
    el.textContent = format(p < 1 ? value * ease(p) : value);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

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
  const headers = { Accept: "application/json", "X-ProcessLens-Language": LANG };
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
    throw new ApiError(0, "network_error", t("err.network"));
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(res.status, err.code || "http_error", err.message || t("err.request_failed", { status: res.status }), err.details);
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
    else if (k === "style" && typeof v === "object") {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith("--")) el.style.setProperty(prop, val); // custom properties need setProperty
        else el.style[prop] = val;
      }
    }
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
const nf = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });
const pctFormats = {};

export const fmtInt = (v) => (v === null || v === undefined ? "—" : nf.format(v));
const nfCache = { 1: nf1, 2: nf2 };
export function fmtNum(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  nfCache[d] ||= new Intl.NumberFormat(LOCALE, { maximumFractionDigits: d });
  return nfCache[d].format(v);
}
export function fmtPct(v, d = 1) {
  if (v === null || v === undefined) return "—";
  pctFormats[d] ||= new Intl.NumberFormat(LOCALE, { style: "percent", minimumFractionDigits: d, maximumFractionDigits: d });
  return pctFormats[d].format(v);
}

export function fmtDur(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.abs(seconds);
  const sign = seconds < 0 ? "−" : "";
  if (s < 60) return sign + t("unit.seconds", { n: nf.format(s) });
  if (s < 3600) return sign + t("unit.minutes", { n: nf1.format(s / 60) });
  if (s < 86400 * 2) return sign + t("unit.hours", { n: nf1.format(s / 3600) });
  return sign + t("unit.days", { n: nf1.format(s / 86400) });
}

export function fmtTs(iso, tz, { seconds = false, dateOnly = false } = {}) {
  if (!iso) return "—";
  const d = new Date(iso);
  const opts = dateOnly
    ? { year: "numeric", month: "short", day: "2-digit", timeZone: tz || "UTC" }
    : { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
        ...(seconds ? { second: "2-digit" } : {}), hour12: false, timeZone: tz || "UTC" };
  try {
    return new Intl.DateTimeFormat(LOCALE, opts).format(d);
  } catch {
    return iso;
  }
}

export const STATUS_LABEL = { completed: t("status.completed"), incomplete: t("status.incomplete"), unknown: t("status.unknown") };
export const RESULT_LABEL = { compliant: t("result.compliant"), violating: t("result.violating"), not_evaluable: t("result.not_evaluable") };
export const SEVERITY_LABEL = { error: t("severity.error"), warning: t("severity.warning"), info: t("severity.info"),
  low: t("severity.low"), medium: t("severity.medium"), high: t("severity.high") };

export function badge(text, cls = "") {
  return h("span", { class: `badge ${cls}` }, text);
}

// ---------------------------------------------------------------- UI states
export function loading(el, text = t("common.loading")) {
  return clear(el, h("div", { class: "state" }, h("span", { class: "spinner" }), " ", text));
}

export function empty(el, title, hint, action) {
  return clear(el, h("div", { class: "state" }, h("div", { class: "title" }, title), hint ? h("div", {}, hint) : null,
    action ? h("div", { style: { marginTop: "10px" } }, action) : null));
}

export function showError(el, err) {
  const msg = err instanceof ApiError ? err.message : String(err?.message || err);
  let hint = "";
  if (err?.code === "network_error") hint = t("err.hint.network");
  else if (err?.code === "csrf_failed") hint = t("err.hint.csrf");
  else if (err?.status === 404) hint = t("err.hint.not_found");
  else if (err?.code === "invalid_filter") hint = t("err.hint.filter");
  else if (err?.status >= 500) hint = t("err.hint.server");
  return clear(el, h("div", { class: "notice error", role: "alert" }, h("b", {}, t("err.something_wrong")), " ", msg,
    hint ? h("div", { class: "small" }, hint) : null));
}

export function toast(message, kind = "") {
  const el = h("div", { class: `toast ${kind}`, role: "status" }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4500);
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
  start_from: t("chip.start_from"), start_to: t("chip.start_to"), activity: t("chip.activity"),
  exclude_activity: t("chip.exclude_activity"), resource: t("chip.resource"), department: t("chip.department"),
  variant: t("chip.variant"), min_duration_h: t("chip.min_duration"), max_duration_h: t("chip.max_duration"),
  rework: t("chip.rework"), status: t("chip.status"), conformance_result: t("chip.conformance"),
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
    h("button", { type: "button", "aria-label": t("chip.remove", { label, value }), onclick: remove(key, value) }, "×")));
  for (const [k, v] of Object.entries(f)) {
    if (["transition_from", "transition_to", "conformance_rule", "conformance_result"].includes(k)) continue;
    if (Array.isArray(v)) v.forEach((x) => chip(CHIP_LABELS[k] || k, k, k === "variant" ? `#${x.slice(0, 8)}` : k === "status" ? (STATUS_LABEL[x] || x) : x));
    else chip(CHIP_LABELS[k] || k, k, k === "rework" ? (v === "true" ? t("common.yes") : t("common.no")) : v);
  }
  if (f.transition_from) {
    const name = (s) => (s === "__start__" ? t("graph.start") : s === "__end__" ? t("graph.end") : s);
    chip(t("chip.transition"), "transition", `${name(f.transition_from)} → ${name(f.transition_to)}`);
  }
  if (f.conformance_rule && f.conformance_result) {
    const rule = filterOptions?.conformance_rules?.find((r) => String(r.id) === String(f.conformance_rule));
    chip(t("chip.conformance"), "conformance", `${rule ? rule.name : t("chip.rule_id", { id: f.conformance_rule })}: ${f.conformance_result.map((r) => RESULT_LABEL[r] || r).join(", ")}`);
  }
  if (chips.length) {
    chips.push(h("button", { type: "button", class: "btn-ghost btn-sm", onclick: () => applyFilters({}) }, t("filters.clear_all")));
  } else {
    chips.push(h("span", { class: "muted small" }, t("filters.none")));
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
    ds.is_demo ? h("span", { class: "badge synthetic", title: t("top.synthetic_title") }, t("top.synthetic")) : null,
    h("span", { class: "badge", title: t("top.version_title", { version: ds.version }) }, `v${ds.version}`),
    h("span", { class: "muted small" }, t("top.dataset_summary", { cases: fmtInt(ds.case_count), events: fmtInt(ds.event_count), tz: ds.display_timezone })),
    ds.quarantined_count ? h("span", { class: "badge warn", title: t("top.quarantined_title") }, t("top.quarantined", { count: fmtInt(ds.quarantined_count) })) : null);
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
      : h("option", { value: "" }, t("top.no_datasets")));
    sel.disabled = !datasetsCache.length;
  }
  if (id !== currentDatasetId()) setUrl({ dataset: id }, { replace: true });
  else if (!id) safeStorage("del", "processlens.dataset");
  renderDatasetBadges();
  return datasetsCache;
}

export function noDatasetState(el) {
  return empty(el, t("empty.no_dataset.title"), t("empty.no_dataset.hint"),
    h("div", { class: "actions", style: { justifyContent: "center" } },
      h("a", { class: "btn btn-primary", href: "/datasets?demo=1" }, t("ds.load_demo")),
      h("a", { class: "btn", href: "/import" }, t("ds.import_csv"))));
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
    h("span", { class: "muted" }, t("pager.summary", { rows: fmtInt(total), page, pages })),
    h("button", { type: "button", class: "btn-sm", disabled: page <= 1, onclick: () => onPage(page - 1) }, t("pager.prev")),
    h("button", { type: "button", class: "btn-sm", disabled: page >= pages, onclick: () => onPage(page + 1) }, t("pager.next")));
}

export function variantSequence(activities) {
  const seen = new Set();
  const parts = [];
  activities.forEach((a, i) => {
    if (i) parts.push(h("span", { class: "arrow", "aria-hidden": "true" }, "→"));
    parts.push(h("span", { class: `step ${seen.has(a) ? "repeat" : ""}`, title: seen.has(a) ? t("variant.repeated_activity") : null }, a));
    seen.add(a);
  });
  return h("div", { class: "seq", "aria-label": activities.join(" → ") }, parts);
}

export function exportLinks(datasetId, kinds, extra = {}) {
  const p = filterParams();
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  const qs = p.toString() ? `?${p.toString()}` : "";
  return h("div", { class: "actions" }, h("span", { class: "muted small" }, t("export.filtered")),
    kinds.map(([kind, fmt, label]) => h("a", { class: "btn btn-sm", href: `/api/datasets/${datasetId}/exports/${kind}.${fmt}${qs}`,
      download: "" }, label)));
}
