// ProcessLens Miner shared utilities. Every number comes from the API; this module
// only fetches, formats and renders. DOM is built with textContent (never innerHTML
// with data) to avoid HTML injection.

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
const LOCALE = navigator.language || "en-US";

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
  get position() { return this.details?.position ?? null; }
}

export async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (method !== "GET") opts.headers["X-CSRF-Token"] = CSRF;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, "network", "Miner is not reachable. Is `python -m miner` still running?");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const e = data?.error || {};
    throw new ApiError(res.status, e.code || "error", e.message || `Request failed (${res.status})`, e.details);
  }
  return data;
}

// ---------------------------------------------------------------- DOM
export const $ = (sel, root = document) => root.querySelector(sel);

/** h("div", {class: "x", onclick: fn}, child, "text", [children]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = Boolean(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export function toast(message, kind = "info") {
  const el = h("div", { class: `toast ${kind}` }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

export function errorToast(err) {
  toast(err?.message || String(err), "error");
}

/** Modal dialog. ``build(body, close)`` fills the body; returns a promise resolved with close(value). */
export function dialog({ title, wide = false, build, actions = [] }) {
  return new Promise((resolve) => {
    const body = h("div", { class: "dialog-body" });
    const foot = h("div", { class: "dialog-foot" });
    const dlg = h("dialog", { class: wide ? "wide" : "", "aria-label": title },
      h("div", { class: "dialog-head" }, h("h2", {}, title),
        h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "Close", onclick: () => close(null) }, "✕")),
      body, foot);
    let done = false;
    const close = (value) => {
      if (done) return;
      done = true;
      dlg.close();
      dlg.remove();
      resolve(value);
    };
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(null); });
    build(body, close, foot);
    for (const a of actions) {
      foot.append(h("button", { type: "button", class: `btn ${a.primary ? "btn-primary" : a.danger ? "btn-danger" : ""}`,
        onclick: async (e) => {
          if (!a.run) return close(null);
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            const v = await a.run();
            if (v !== undefined) close(v);
          } catch (err) {
            errorToast(err);
          } finally { btn.disabled = false; }
        } }, a.label));
    }
    document.body.append(dlg);
    dlg.showModal();
    dlg.querySelector("input, textarea, select")?.focus();
  });
}

export async function confirmDialog(title, message, label = "Delete") {
  return dialog({ title, build: (b) => b.append(h("p", {}, message)),
    actions: [{ label: "Cancel" }, { label, danger: true, run: () => true }] });
}

export function debounce(fn, ms = 300) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------- formatting
const nf = (opts) => new Intl.NumberFormat(LOCALE, opts);
const INT = nf({ maximumFractionDigits: 0 });
const NUM = nf({ maximumFractionDigits: 2 });
const PCT = nf({ style: "percent", maximumFractionDigits: 1 });
const CUR = nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmtValue(v, format = "auto", type = "number") {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (type === "datetime" && typeof v === "string") return fmtDate(v);
  if (typeof v !== "number") return String(v);
  switch (format) {
    case "integer": return INT.format(v);
    case "percent": return PCT.format(v);
    case "currency": return CUR.format(v);
    case "days": return `${NUM.format(v)} d`;
    case "hours": return `${NUM.format(v)} h`;
    case "text": return String(v);
    default: return Number.isInteger(v) ? INT.format(v) : NUM.format(v);
  }
}

export function fmtDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(LOCALE, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const fmtInt = (v) => fmtValue(v, "integer");

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const PALETTE = () => ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"].map(cssVar);

/** PLQL text literal for a value (mirrors miner.plql.parser.quote). */
export function plqlLiteral(v) {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  if (typeof v === "number") return String(v);
  return `"${String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n")}"`;
}

/** Condition "formula equals value" for cross-filter selections. */
export function equalsCondition(formula, value) {
  if (value === null || value === undefined) return `ISNULL(${formula})`;
  if (typeof value === "boolean") return value ? `(${formula}) = TRUE` : `(${formula}) = FALSE`;
  return `(${formula}) = ${plqlLiteral(value)}`;
}

export function downloadText(filename, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function toCsv(columns, rows) {
  const cell = (v) => {
    let s = v === null || v === undefined ? "" : String(v);
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`; // keep spreadsheets from running text as formulas
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map((c) => cell(c.label)).join(","), ...rows.map((r) => r.map(cell).join(","))].join("\n");
}

// ---------------------------------------------------------------- theme
export function initTheme() {
  let stored = null;
  try { stored = localStorage.getItem("miner-theme"); } catch { /* storage blocked */ }
  if (stored) document.documentElement.dataset.theme = stored;
  $("#theme-toggle")?.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("miner-theme", next); } catch { /* storage blocked */ }
    window.dispatchEvent(new Event("themechange"));
  });
}
