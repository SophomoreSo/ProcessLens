// Knowledge model editor: KPIs (measures) and saved filters (conditions), written in PLQL.
import { go, refreshPackage, setCrumbs } from "./app.js";
import { api, clear, confirmDialog, dialog, errorToast, fmtInt, fmtValue, h, toast } from "./core.js";
import { formulaInput, referencePanel } from "./formula.js";

const FORMATS = [["auto", "Automatic"], ["integer", "Integer"], ["number", "Number"], ["percent", "Percent (0..1)"],
  ["days", "Days"], ["hours", "Hours"], ["currency", "Currency"]];

export function slug(text) {
  const s = text.toLowerCase().normalize("NFKD").replace(/[^\w\s]/g, "").trim().replace(/\s+/g, "_").replace(/^[^a-z]+/, "");
  return s.slice(0, 64) || "item";
}

export function formatSelect(value = "auto") {
  const sel = h("select", {}, FORMATS.map(([v, l]) => h("option", { value: v }, l)));
  sel.value = value;
  return sel;
}

async function editItem(ctx, kind, item = null) {
  const pkg = ctx.pkg;
  const isKpi = kind === "kpi";
  const name = h("input", { value: item?.name || "", maxlength: 200, required: true });
  const key = h("input", { value: item?.key || "", maxlength: 64, pattern: "[a-z][a-z0-9_]*", required: true,
    class: "mono", title: "Lower-case letters, digits and _; starts with a letter" });
  let keyTouched = Boolean(item);
  key.addEventListener("input", () => { keyTouched = true; });
  name.addEventListener("input", () => { if (!keyTouched) key.value = slug(name.value); });
  const formula = formulaInput({ value: item?.formula || "", rows: 4, label: isKpi ? "Formula (an aggregation)" : "Condition",
    placeholder: isKpi ? 'e.g. SHARE(case.duration_d <= 14)' : 'e.g. CONTAINS("Change price") AND case.duration_d > 30',
    onRun: () => test() });
  const format = formatSelect(item?.format || "auto");
  const target = h("input", { type: "number", step: "any", value: item?.target ?? "" });
  const better = h("select", {}, h("option", { value: "true" }, "Higher is better"), h("option", { value: "false" }, "Lower is better"));
  better.value = String(item?.higher_is_better ?? true);
  const desc = h("textarea", { rows: 2, maxlength: 4000 });
  desc.value = item?.description || "";

  async function test() {
    if (!formula.value) return;
    try {
      const r = await api(`/api/packages/${pkg.id}/evaluate`, { method: "POST",
        body: { formula: formula.value, kind: isKpi ? "measure" : "condition", key: key.value || null } });
      formula.ok(isKpi ? `= ${fmtValue(r.value, format.value === "auto" ? "number" : format.value, r.type)}  (over ${fmtInt(r.case_count)} cases, ${r.elapsed_ms} ms)`
        : `Keeps ${fmtInt(r.matching)} of ${fmtInt(r.total)} cases (${r.total ? ((100 * r.matching) / r.total).toFixed(1) : 0} %).`);
    } catch (err) { formula.error(err); }
  }

  const saved = await dialog({
    title: item ? `Edit ${isKpi ? "KPI" : "filter"}` : `New ${isKpi ? "KPI" : "filter"}`,
    wide: true,
    build: (body) => body.append(h("div", { class: "dialog-split" },
      h("div", { class: "stack" },
        h("div", { class: "form-grid" }, h("label", {}, "Name", name), h("label", {}, "Key (for references)", key)),
        formula.el,
        h("button", { type: "button", class: "btn btn-sm", onclick: test }, "Test formula ", h("span", { class: "kbd" }, "⌘/Ctrl+Enter")),
        isKpi ? h("div", { class: "form-grid" }, h("label", {}, "Format", format), h("label", {}, "Target (optional)", target),
          h("label", {}, "Direction", better)) : null,
        h("label", {}, "Description", desc)),
      referencePanel(ctx.reference, { schema: ctx.schema, kpis: pkg.kpis.filter((k) => k.key !== item?.key || !isKpi),
        filters: pkg.filters.filter((f) => f.key !== item?.key || isKpi) }))),
    actions: [{ label: "Cancel" }, { label: "Save", primary: true, run: async () => {
      const body = { key: key.value.trim(), name: name.value.trim(), formula: formula.value, description: desc.value };
      if (isKpi) Object.assign(body, { format: format.value, higher_is_better: better.value === "true",
        target: target.value === "" ? null : Number(target.value) });
      const base = `/api/packages/${pkg.id}/${isKpi ? "kpis" : "filters"}`;
      try {
        return await api(item ? `${base}/${item.id}` : base, { method: item ? "PUT" : "POST", body });
      } catch (err) {
        if (err.code === "plql_error") { formula.error(err); return undefined; }
        throw err;
      }
    } }],
  });
  if (saved) {
    toast(`${isKpi ? "KPI" : "Filter"} “${saved.name}” saved`);
    await refreshPackage();
    go(`#/p/${pkg.id}/km/${isKpi ? "kpis" : "filters"}`);
  }
}

async function removeItem(ctx, kind, item) {
  if (!await confirmDialog(`Delete ${kind === "kpi" ? "KPI" : "filter"}`, `Delete “${item.name}”?`)) return;
  await api(`/api/packages/${ctx.pkg.id}/${kind === "kpi" ? "kpis" : "filters"}/${item.id}`, { method: "DELETE" });
  toast("Deleted");
  await refreshPackage();
  go(location.hash);
}

export async function renderKnowledge(canvas, ctx, tab) {
  const pkg = ctx.pkg;
  const isKpi = tab !== "filters";
  setCrumbs(["Knowledge model", null], [isKpi ? "KPIs" : "Filters", null]);
  const items = isKpi ? pkg.kpis : pkg.filters;
  const tbody = h("tbody", {});
  const table = h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Key"), h("th", {}, isKpi ? "Formula" : "Condition"),
      h("th", { class: "num" }, isKpi ? "Value" : "Cases"), isKpi ? h("th", { class: "num" }, "Target") : null, h("th", {}, ""))),
    tbody));
  const valueCells = new Map();
  for (const it of items) {
    const cell = h("td", { class: "num" }, h("span", { class: "muted" }, "…"));
    valueCells.set(it.key, cell);
    tbody.append(h("tr", {},
      h("td", {}, h("b", {}, it.name), it.description ? h("div", { class: "small muted" }, it.description) : null),
      h("td", {}, h("code", {}, it.key)),
      h("td", { class: "formula-cell" }, it.formula),
      cell,
      isKpi ? h("td", { class: "num" }, it.target === null || it.target === undefined ? "—"
        : `${it.higher_is_better ? "≥" : "≤"} ${fmtValue(it.target, it.format === "auto" ? "number" : it.format)}`) : null,
      h("td", { class: "nowrap right" },
        h("button", { type: "button", class: "btn btn-sm", onclick: () => editItem(ctx, isKpi ? "kpi" : "filter", it).catch(errorToast) }, "Edit"),
        " ",
        h("button", { type: "button", class: "btn btn-sm btn-ghost", "aria-label": `Delete ${it.name}`,
          onclick: () => removeItem(ctx, isKpi ? "kpi" : "filter", it).catch(errorToast) }, "🗑"))));
  }
  const tabs = h("div", { class: "tabs", role: "tablist" },
    h("button", { role: "tab", "aria-selected": String(isKpi), onclick: () => go(`#/p/${pkg.id}/km/kpis`) }, `KPIs (${pkg.kpis.length})`),
    h("button", { role: "tab", "aria-selected": String(!isKpi), onclick: () => go(`#/p/${pkg.id}/km/filters`) }, `Filters (${pkg.filters.length})`));
  clear(canvas,
    h("div", { class: "canvas-head" },
      h("div", { class: "titles" }, h("h1", {}, "Knowledge model"),
        h("p", { class: "muted" }, isKpi
          ? "KPIs are named aggregations. Reuse them anywhere as KPI(\"key\") — change a KPI here and every view follows."
          : "Filters are named case conditions. Use them in views, in queries and inside formulas as FILTER(\"key\").")),
      h("button", { type: "button", class: "btn btn-primary", onclick: () => editItem(ctx, isKpi ? "kpi" : "filter").catch(errorToast) },
        isKpi ? "+ New KPI" : "+ New filter")),
    tabs,
    items.length ? table : h("div", { class: "card empty" }, h("p", {}, isKpi ? "No KPIs yet." : "No filters yet.")));

  if (!items.length || !ctx.schema) return;
  try {
    if (isKpi) {
      const res = await api(`/api/packages/${pkg.id}/kpi-values`);
      for (const [k, cell] of valueCells) {
        const v = res.values[k] || {};
        clear(cell, v.error ? h("span", { class: "comp-error", title: v.error }, "error") : fmtValue(v.value, v.format));
      }
    } else {
      const res = await api(`/api/packages/${pkg.id}/filter-counts`);
      for (const [k, cell] of valueCells) {
        const v = res.counts[k] || {};
        clear(cell, v.error ? h("span", { class: "comp-error", title: v.error }, "error")
          : `${fmtInt(v.matching)} / ${fmtInt(v.total)}`);
      }
    }
  } catch (err) {
    for (const cell of valueCells.values()) clear(cell, "—");
    errorToast(err);
  }
}
