// Views: dashboards of components with saved-filter toggles and click-to-filter
// selections (view mode), and a grid builder with a component editor (edit mode).
import { go, refreshPackage, setCrumbs, setLeaveGuard } from "./app.js";
import { COMPONENT_TYPES, renderComponent } from "./components.js";
import { api, clear, confirmDialog, dialog, errorToast, fmtInt, h, toast } from "./core.js";
import { formulaInput, referencePanel } from "./formula.js";
import { formatSelect } from "./knowledge.js";

const WIDTHS = [3, 4, 6, 8, 12];
const DEFAULTS = {
  kpi: { width: 3, config: { measure: { formula: "COUNT_CASES()", label: "", format: "auto" } } },
  bar: { width: 6, config: { dimension: { formula: "event.activity", label: "" }, measures: [{ formula: "COUNT_CASES()", label: "Cases", format: "auto" }], limit: 15 } },
  line: { width: 8, config: { dimension: { formula: "MONTH(case.start)", label: "Month" }, measures: [{ formula: "COUNT_CASES()", label: "Cases", format: "auto" }], limit: 120 } },
  pie: { width: 4, config: { dimension: { formula: "case.status", label: "" }, measures: [{ formula: "COUNT_CASES()", label: "Cases", format: "auto" }], limit: 8 } },
  table: { width: 12, config: { dimensions: [{ formula: "event.resource", label: "Resource" }], measures: [{ formula: "COUNT_CASES()", label: "Cases", format: "auto" }], limit: 50 } },
  process: { width: 12, config: { max_activities: 15 } },
  variants: { width: 12, config: { limit: 8 } },
  text: { width: 12, config: { text: "" } },
};

function newId(components) {
  let i = components.length + 1;
  while (components.some((c) => c.id === `c${i}`)) i += 1;
  return `c${i}`;
}

// ---------------------------------------------------------------- component editor
export function itemRows(items, { withFormat, kind, kpis, max }) {
  const list = h("div", {});
  const rows = [];
  const add = (item = { formula: "", label: "", format: "auto" }) => {
    if (rows.length >= max) return;
    const f = formulaInput({ value: item.formula, rows: 1, label: null,
      placeholder: kind === "dimension" ? "e.g. event.activity" : "e.g. COUNT_CASES()" });
    const lbl = h("input", { value: item.label || "", placeholder: "Label", maxlength: 200, "aria-label": "Label" });
    const fmt = withFormat ? formatSelect(item.format || "auto") : null;
    const row = { f, lbl, fmt };
    const rm = h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": `Remove ${kind}`, onclick: () => {
      rows.splice(rows.indexOf(row), 1);
      el.remove();
    } }, "✕");
    const el = h("div", { class: `item-row${withFormat ? "" : " no-format"}` }, f.el, lbl, fmt, rm);
    rows.push(row);
    list.append(el);
  };
  for (const it of items) add(it);
  const kpiPick = kind === "measure" && kpis.length ? h("select", { "aria-label": "Add a KPI", onchange: (e) => {
    if (!e.target.value) return;
    const k = kpis.find((x) => x.key === e.target.value);
    add({ formula: `KPI("${k.key}")`, label: k.name, format: "auto" });
    e.target.value = "";
  } }, h("option", { value: "" }, "+ Add a KPI…"), kpis.map((k) => h("option", { value: k.key }, k.name))) : null;
  return {
    el: h("div", {}, list, h("div", { class: "row" },
      h("button", { type: "button", class: "btn btn-sm", onclick: () => add() }, `+ ${kind === "dimension" ? "Dimension" : "Measure"}`), kpiPick)),
    get values() {
      return rows.map((r) => ({ formula: r.f.value, label: r.lbl.value.trim(), ...(r.fmt ? { format: r.fmt.value } : {}) }))
        .filter((x) => x.formula);
    },
    rows,
  };
}

async function editComponent(ctx, comp, components) {
  const pkg = ctx.pkg;
  let type = comp?.type || "kpi";
  let state = comp ? structuredClone(comp) : null;
  const titleIn = h("input", { maxlength: 200, placeholder: "Title (optional)" });
  const widthSel = h("select", {}, WIDTHS.map((w) => h("option", { value: w }, `${w} / 12 columns`)));
  const compFilter = formulaInput({ rows: 1, label: "Component filter (optional, PLQL condition)",
    placeholder: 'e.g. case.status = "completed"' });
  const picker = h("div", { class: "type-picker", role: "radiogroup", "aria-label": "Component type" });
  const fieldsBox = h("div", { class: "stack" });
  let fields = {};

  const buildFields = () => {
    const cfg = state?.type === type ? state.config : structuredClone(DEFAULTS[type].config);
    fields = {};
    const parts = [];
    if (type === "kpi") {
      const m = cfg.measure || {};
      const kpiSel = h("select", { "aria-label": "KPI" }, h("option", { value: "" }, "Custom formula…"),
        pkg.kpis.map((k) => h("option", { value: k.key }, k.name)));
      const f = formulaInput({ value: m.formula || "", rows: 2, label: "Measure" });
      const match = /^\s*KPI\(\s*"([^"]+)"\s*\)\s*$/i.exec(m.formula || "");
      if (match) kpiSel.value = match[1];
      kpiSel.addEventListener("change", () => { if (kpiSel.value) f.value = `KPI("${kpiSel.value}")`; });
      const lbl = h("input", { value: m.label || "", maxlength: 200, placeholder: "Defaults to the KPI name" });
      const fmt = formatSelect(m.format || "auto");
      fields.get = () => ({ measure: { formula: f.value, label: lbl.value.trim(), format: fmt.value } });
      fields.formulas = [f];
      parts.push(h("label", {}, "KPI", kpiSel), f.el, h("div", { class: "form-grid" }, h("label", {}, "Label", lbl), h("label", {}, "Format", fmt)));
    } else if (["bar", "line", "pie", "table"].includes(type)) {
      const dims = itemRows(type === "table" ? (cfg.dimensions || []) : (cfg.dimension ? [cfg.dimension] : []),
        { withFormat: false, kind: "dimension", kpis: pkg.kpis, max: type === "table" ? 8 : 1 });
      const meas = itemRows(cfg.measures || [], { withFormat: true, kind: "measure", kpis: pkg.kpis, max: type === "pie" ? 1 : 10 });
      const limit = h("input", { type: "number", min: 1, max: 5000, value: cfg.limit || 20 });
      const order = h("select", {}, h("option", { value: "auto" }, "Automatic"), h("option", { value: "measure" }, "By first measure (largest first)"),
        h("option", { value: "dimension" }, "By dimension"));
      order.value = cfg.order || "auto";
      const orient = h("select", {}, h("option", { value: "auto" }, "Automatic"), h("option", { value: "h" }, "Horizontal"), h("option", { value: "v" }, "Vertical"));
      orient.value = cfg.orientation || "auto";
      fields.get = () => {
        const d = dims.values;
        const out = { measures: meas.values, limit: Number(limit.value) || 20, order: order.value };
        if (type === "table") out.dimensions = d; else out.dimension = d[0] || null;
        if (type === "bar") out.orientation = orient.value;
        return out;
      };
      fields.formulas = () => [...dims.rows.map((r) => r.f), ...meas.rows.map((r) => r.f)];
      if (type !== "table" && !dims.rows.length) dims.el.querySelector("button").click();
      parts.push(h("h3", {}, type === "table" ? "Dimensions (group by)" : "Dimension"), dims.el,
        h("h3", {}, "Measures"), meas.el,
        h("div", { class: "form-grid" }, h("label", {}, "Rows / bars (max)", limit), h("label", {}, "Order", order),
          type === "bar" ? h("label", {}, "Orientation", orient) : null));
    } else if (type === "process") {
      const n = h("input", { type: "number", min: 2, max: 60, value: cfg.max_activities || 15 });
      fields.get = () => ({ max_activities: Number(n.value) || 15 });
      parts.push(h("label", {}, "Max activities (most frequent first)", n));
    } else if (type === "variants") {
      const n = h("input", { type: "number", min: 1, max: 100, value: cfg.limit || 8 });
      fields.get = () => ({ limit: Number(n.value) || 8 });
      parts.push(h("label", {}, "Variants to show", n));
    } else {
      const t = h("textarea", { rows: 6, maxlength: 4000 });
      t.value = cfg.text || "";
      fields.get = () => ({ text: t.value });
      parts.push(h("label", {}, "Text", t));
    }
    clear(fieldsBox, parts);
  };

  const renderPicker = () => clear(picker, COMPONENT_TYPES.map(([t, name, desc]) => h("button", {
    type: "button", role: "radio", "aria-checked": String(t === type), "aria-pressed": String(t === type),
    onclick: () => {
      if (t === type) return;
      state = { ...(state || {}), type, config: fields.get?.() };
      type = t;
      renderPicker();
      buildFields();
      if (!comp) widthSel.value = String(DEFAULTS[t].width);
    } }, h("b", {}, name), h("small", {}, desc))));

  titleIn.value = comp?.title || "";
  widthSel.value = String(comp?.width || DEFAULTS[type].width);
  compFilter.value = comp?.filter || "";
  renderPicker();
  buildFields();

  return dialog({
    title: comp ? "Edit component" : "Add component",
    wide: true,
    build: (body) => body.append(h("div", { class: "dialog-split" },
      h("div", { class: "stack" }, picker,
        h("div", { class: "form-grid" }, h("label", {}, "Title", titleIn), h("label", {}, "Width", widthSel)),
        fieldsBox, compFilter.el),
      referencePanel(ctx.reference, { schema: ctx.schema, kpis: pkg.kpis, filters: pkg.filters }))),
    actions: [{ label: "Cancel" }, { label: comp ? "Apply" : "Add", primary: true, run: async () => {
      const next = { id: comp?.id || newId(components), type, title: titleIn.value.trim(),
        width: Number(widthSel.value), filter: compFilter.value, config: fields.get() };
      // Check the component against the data before accepting it.
      try {
        await api(`/api/packages/${pkg.id}/data`, { method: "POST", body: { component: next, filters: [] } });
      } catch (err) {
        if (err.code === "plql_error") {
          const msg = err.message;
          const fs = typeof fields.formulas === "function" ? fields.formulas() : (fields.formulas || []);
          const m = /^(Dimension|Measure) (\d+): /.exec(msg);
          let target = null;
          if (m) {
            const dimsCount = type === "table" ? (next.config.dimensions || []).length : 1;
            const idx = m[1] === "Dimension" ? Number(m[2]) - 1 : dimsCount + Number(m[2]) - 1;
            target = type === "kpi" ? fs[0] : fs.filter((f) => f.value)[idx];
          }
          (target || (next.filter ? compFilter : fs[0]))?.error(err);
          return undefined;
        }
        if (err.code !== "dataset_unavailable") throw err;
      }
      return next;
    } }],
  });
}

// ---------------------------------------------------------------- view page
export async function renderView(canvas, ctx, viewId, startInEdit) {
  const pkg = ctx.pkg;
  const view = await api(`/api/packages/${pkg.id}/views/${viewId}`);
  setCrumbs([view.name, null]);
  let editing = startInEdit;
  let dirty = false;
  const active = new Set();
  const selections = [];
  const cleanups = new Map();
  const cache = new Map();
  let generation = 0;

  setLeaveGuard((silent) => !dirty || (!silent && window.confirm("Leave without saving the changes to this view?")));

  const nameIn = h("input", { value: view.name, maxlength: 200, "aria-label": "View name", style: "font-size:1.15rem;font-weight:600;min-width:260px" });
  const descIn = h("input", { value: view.description || "", maxlength: 4000, placeholder: "Description", "aria-label": "View description", style: "min-width:320px" });
  const viewFilter = formulaInput({ value: view.filter || "", rows: 1, label: "View filter (always applied in this view)",
    placeholder: 'e.g. case.start >= "2024-01-01"', onInput: () => { dirty = true; } });
  [nameIn, descIn].forEach((el) => el.addEventListener("input", () => { dirty = true; }));

  const head = h("div", { class: "canvas-head" });
  const bar = h("div", { class: "view-bar" });
  const grid = h("div", { class: "view-grid" });
  clear(canvas, head, bar, grid);

  const conditions = () => [
    ...(view.filter ? [view.filter] : []),
    ...[...active].map((k) => `FILTER("${k}")`),
    ...selections.map((s) => s.condition),
  ];

  const save = async () => {
    try {
      const saved = await api(`/api/packages/${pkg.id}/views/${view.id}`, { method: "PUT", body: {
        name: nameIn.value.trim() || view.name, description: descIn.value, filter: viewFilter.value, components: view.components } });
      Object.assign(view, saved);
      dirty = false;
      editing = false;
      await refreshPackage();
      setCrumbs([view.name, null]);
      toast("View saved");
      history.replaceState(null, "", `#/p/${pkg.id}/v/${view.id}`);
      renderAll();
    } catch (err) {
      if (err.code === "plql_error") viewFilter.error(err); else errorToast(err);
    }
  };

  const discard = () => {
    if (dirty && !window.confirm("Discard the changes to this view?")) return;
    dirty = false;
    go(`#/p/${pkg.id}/v/${view.id}`);
  };

  function renderHead() {
    clear(head,
      h("div", { class: "titles" }, editing
        ? h("div", { class: "row" }, nameIn, descIn)
        : [h("h1", {}, view.name), view.description ? h("p", { class: "muted" }, view.description) : null]),
      h("div", { class: "row" }, editing
        ? [h("button", { type: "button", class: "btn", onclick: discard }, "Discard"),
          h("button", { type: "button", class: "btn btn-primary", onclick: save }, "Save view")]
        : [h("button", { type: "button", class: "btn", onclick: duplicate }, "Duplicate"),
          h("button", { type: "button", class: "btn btn-danger", onclick: () => remove().catch(errorToast) }, "Delete"),
          h("button", { type: "button", class: "btn btn-primary", onclick: () => { editing = true; renderAll(); } }, "✎ Edit")]));
  }

  const caseInfo = h("span", { class: "small muted" });
  function renderBar() {
    clear(bar,
      pkg.filters.length ? h("span", { class: "small muted" }, "Filters:") : null,
      h("div", { class: "chips" }, pkg.filters.map((f) => h("button", { type: "button", class: "chip", title: f.formula,
        "aria-pressed": String(active.has(f.key)), onclick: () => {
          if (active.has(f.key)) active.delete(f.key); else active.add(f.key);
          renderBar();
          loadAll();
        } }, f.name)),
      selections.map((s, i) => h("span", { class: "chip selection", title: s.condition }, s.label,
        h("button", { type: "button", class: "x", "aria-label": `Remove selection ${s.label}`, onclick: () => {
          selections.splice(i, 1);
          renderBar();
          loadAll();
        } }, "×")))),
      selections.length || active.size ? h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: () => {
        selections.length = 0;
        active.clear();
        renderBar();
        loadAll();
      } }, "Clear all") : null,
      h("span", { class: "grow" }),
      view.filter && !editing ? h("span", { class: "badge warn", title: view.filter }, "view filter") : null,
      caseInfo,
      editing ? h("div", { style: "flex-basis:100%" }, viewFilter.el) : null);
  }

  const onSelect = (label, condition) => {
    if (editing) return;
    if (selections.some((s) => s.condition === condition)) return;
    selections.push({ label, condition });
    renderBar();
    loadAll();
  };

  function compShell(comp, index) {
    const body = h("div", { class: "comp-body" }, h("div", { class: "loading" }, "Loading…"));
    const tools = editing ? h("div", { class: "comp-tools" },
      h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Move left", "aria-label": "Move left",
        disabled: index === 0, onclick: () => move(index, -1) }, "←"),
      h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Move right", "aria-label": "Move right",
        disabled: index === view.components.length - 1, onclick: () => move(index, 1) }, "→"),
      h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Change width", "aria-label": "Change width",
        onclick: () => { comp.width = WIDTHS[(WIDTHS.indexOf(comp.width) + 1) % WIDTHS.length]; dirty = true; renderAll(); } }, "↔"),
      h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Edit", "aria-label": "Edit component",
        onclick: () => edit(index) }, "✎"),
      h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Remove", "aria-label": "Remove component",
        onclick: () => { view.components.splice(index, 1); dirty = true; renderAll(); } }, "🗑")) : null;
    const title = comp.title || (comp.type === "kpi" ? "" : COMPONENT_TYPES.find(([t]) => t === comp.type)?.[1]);
    const el = h("section", { class: `comp w${comp.width || 6}${editing ? " editing" : ""}`, "aria-label": title || comp.type },
      h("div", { class: "comp-head" }, h("h3", { class: "comp-title" }, title || ""),
        comp.filter ? h("span", { class: "badge", title: comp.filter }, "filtered") : null, tools),
      body);
    return { el, body };
  }

  const shells = new Map();
  function renderGrid() {
    for (const fn of cleanups.values()) { try { fn?.(); } catch { /* ignore */ } }
    cleanups.clear();
    shells.clear();
    clear(grid);
    view.components.forEach((comp, i) => {
      const s = compShell(comp, i);
      shells.set(comp.id, s);
      grid.append(s.el);
    });
    if (editing) grid.append(h("button", { type: "button", class: "add-comp", onclick: () => edit(-1) }, "+ Add component"));
    if (!view.components.length && !editing) {
      grid.append(h("div", { class: "card empty", style: "grid-column: span 12" }, h("h2", {}, "This view is empty"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => { editing = true; renderAll(); } }, "✎ Start building")));
    }
  }

  function draw(comp, data) {
    const s = shells.get(comp.id);
    if (!s) return;
    cleanups.get(comp.id)?.();
    if (comp.type === "kpi") {
      const t = s.el.querySelector(".comp-title");
      if (!comp.title) t.textContent = data.name;
      if (data.description) t.title = data.description;
    }
    cleanups.set(comp.id, renderComponent(s.body, comp, data, onSelect));
  }

  async function loadAll() {
    const gen = ++generation;
    const conds = conditions();
    caseInfo.textContent = "";
    let infoSet = false;
    await Promise.all(view.components.map(async (comp) => {
      const s = shells.get(comp.id);
      if (!s) return;
      if (comp.type === "text") { draw(comp, {}); return; }
      try {
        const data = await api(`/api/packages/${pkg.id}/data`, { method: "POST", body: { component: comp, filters: conds } });
        if (gen !== generation) return;
        cache.set(comp.id, data);
        draw(comp, data);
        if (!infoSet && data.case_count !== undefined && data.total_case_count !== undefined && comp.type !== "kpi") {
          infoSet = true;
          caseInfo.textContent = `${fmtInt(data.case_count)} of ${fmtInt(data.total_case_count)} cases`;
        }
      } catch (err) {
        if (gen !== generation) return;
        clear(s.body, h("div", { class: "comp-error" }, err.message));
      }
    }));
  }

  function renderAll() {
    renderHead();
    renderBar();
    renderGrid();
    loadAll();
  }

  async function edit(index) {
    const current = index >= 0 ? view.components[index] : null;
    const next = await editComponent(ctx, current, view.components);
    if (!next) return;
    if (index >= 0) view.components[index] = next; else view.components.push(next);
    dirty = true;
    renderAll();
  }

  function move(index, delta) {
    const [c] = view.components.splice(index, 1);
    view.components.splice(index + delta, 0, c);
    dirty = true;
    renderAll();
  }

  async function duplicate() {
    const copy = await api(`/api/packages/${pkg.id}/views/${view.id}/duplicate`, { method: "POST" });
    await refreshPackage();
    toast("View duplicated");
    go(`#/p/${pkg.id}/v/${copy.id}`);
  }

  async function remove() {
    if (!await confirmDialog("Delete view", `Delete the view “${view.name}”?`)) return;
    await api(`/api/packages/${pkg.id}/views/${view.id}`, { method: "DELETE" });
    dirty = false;
    await refreshPackage();
    toast("View deleted");
    go(`#/p/${pkg.id}`);
  }

  const onTheme = () => { for (const comp of view.components) if (cache.has(comp.id)) draw(comp, cache.get(comp.id)); };
  window.addEventListener("themechange", onTheme);
  renderAll();
  return () => {
    window.removeEventListener("themechange", onTheme);
    for (const fn of cleanups.values()) { try { fn?.(); } catch { /* ignore */ } }
  };
}
