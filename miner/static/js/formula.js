// PLQL formula inputs and the reference panel that inserts columns, functions,
// KPIs, filters and activity names at the cursor of the last focused formula.
import { api, h, plqlLiteral } from "./core.js";

let lastFormula = null;
let referenceCache = null;

export async function loadReference() {
  if (!referenceCache) referenceCache = await api("/api/reference");
  return referenceCache;
}

export function insertAtCursor(textarea, text) {
  if (!textarea) return;
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
  const pos = start + text.length;
  textarea.focus();
  textarea.setSelectionRange(pos, pos);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

/** A formula textarea with a message line below it. */
export function formulaInput({ value = "", placeholder = "", rows = 2, label, onInput, onRun } = {}) {
  const textarea = h("textarea", { class: "formula", rows, spellcheck: "false", autocapitalize: "off",
    autocomplete: "off", placeholder, "aria-label": label || "Formula" });
  textarea.value = value;
  const msg = h("div", { class: "formula-msg", "aria-live": "polite" });
  textarea.addEventListener("focus", () => { lastFormula = textarea; });
  textarea.addEventListener("input", () => {
    textarea.classList.remove("invalid");
    msg.className = "formula-msg";
    msg.replaceChildren();
    onInput?.(textarea.value);
  });
  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && onRun) { e.preventDefault(); onRun(); }
  });
  const wrap = h("div", { class: "formula-wrap" }, label ? h("label", {}, label, textarea) : textarea, msg);
  return {
    el: wrap,
    textarea,
    get value() { return textarea.value.trim(); },
    set value(v) { textarea.value = v; },
    ok(text) { msg.className = "formula-msg ok"; msg.replaceChildren(text); },
    error(err) { showError(textarea, msg, err); },
    clear() { textarea.classList.remove("invalid"); msg.className = "formula-msg"; msg.replaceChildren(); },
  };
}

/** Error message with a caret under the failing position (when the error belongs to this formula). */
export function showError(textarea, msg, err) {
  textarea.classList.add("invalid");
  msg.className = "formula-msg err";
  msg.replaceChildren(err.message || String(err));
  const pos = err.position;
  if (typeof pos === "number" && pos <= textarea.value.length) {
    const text = textarea.value;
    const lineStart = text.lastIndexOf("\n", pos - 1) + 1;
    let lineEnd = text.indexOf("\n", pos);
    if (lineEnd < 0) lineEnd = text.length;
    msg.append(h("pre", {}, `${text.slice(lineStart, lineEnd)}\n${" ".repeat(pos - lineStart)}^`));
  }
}

/** Searchable reference of columns, functions, KPIs, filters and activity names; clicking inserts. */
export function referencePanel(ref, { schema = null, kpis = [], filters = [] } = {}) {
  const groups = [
    ["Case columns", ref.case_columns.map((c) => [c.name, c.description, c.name])],
    ["Event columns", ref.event_columns.map((c) => [c.name, c.description, c.name])],
    ["KPIs", kpis.map((k) => [`KPI("${k.key}")`, k.name, `KPI("${k.key}")`])],
    ["Saved filters", filters.map((f) => [`FILTER("${f.key}")`, f.name, `FILTER("${f.key}")`])],
    ["Aggregations", ref.aggregations.map((f) => [f.signature, f.description, `${f.name}(`])],
    ["Case functions", ref.case_functions.map((f) => [f.signature, f.description, `${f.name}(`])],
    ["Row functions", ref.row_functions.map((f) => [f.signature, f.description, `${f.name}(`])],
  ];
  if (schema) {
    groups.push(["Activities", schema.activities.map((a) => [plqlLiteral(a), "", plqlLiteral(a)])]);
    if (schema.resources.length) groups.push(["Resources", schema.resources.map((a) => [plqlLiteral(a), "", plqlLiteral(a)])]);
  }
  const search = h("input", { type: "search", placeholder: "Search reference…", "aria-label": "Search reference" });
  const body = h("div", {});
  const render = () => {
    const q = search.value.trim().toLowerCase();
    body.replaceChildren(...groups.filter(([, items]) => items.length).map(([title, items], gi) => {
      const shown = items.filter(([name, desc]) => !q || name.toLowerCase().includes(q) || desc.toLowerCase().includes(q));
      if (!shown.length) return null;
      return h("details", { open: q || gi < 2 ? true : null },
        h("summary", {}, `${title} (${shown.length})`),
        h("ul", {}, shown.slice(0, 300).map(([name, desc, insert]) => h("li", {},
          h("button", { type: "button", title: "Insert at the cursor",
            onmousedown: (e) => e.preventDefault(),
            onclick: () => insertAtCursor(lastFormula, insert) },
          h("code", {}, name), desc ? h("small", {}, desc) : null)))));
    }).filter(Boolean));
  };
  search.addEventListener("input", render);
  render();
  return h("div", { class: "reference", "aria-label": "PLQL reference" }, search, body);
}
