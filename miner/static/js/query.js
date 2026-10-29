// Query workbench: dimensions × measures under a filter, as a table or chart; save the
// query, download CSV, or add the result to a view as a component.
import { go, refreshPackage, setCrumbs } from "./app.js";
import { renderComponent } from "./components.js";
import { api, clear, confirmDialog, dialog, downloadText, errorToast, fmtInt, h, toCsv, toast } from "./core.js";
import { formulaInput, referencePanel } from "./formula.js";
import { itemRows } from "./view.js";

const EXAMPLE = {
  dimensions: [{ formula: "event.activity", label: "Activity" }],
  measures: [{ formula: "COUNT_CASES()", label: "Cases", format: "auto" },
    { formula: "COUNT_EVENTS()", label: "Events", format: "auto" },
    { formula: "AVG(event.elapsed_h)", label: "Avg. hours since previous step", format: "hours" }],
  filters: [], limit: 500, order_by: null,
};

export async function renderQuery(canvas, ctx, queryId) {
  const pkg = ctx.pkg;
  const saved = queryId ? pkg.queries.find((q) => q.id === queryId) : null;
  if (queryId && !saved) throw new Error(`Query #${queryId} does not exist in this package.`);
  setCrumbs(["Queries", `#/p/${pkg.id}/q`], ...(saved ? [[saved.name, null]] : [["Workbench", null]]));
  const spec = structuredClone(saved?.spec || EXAMPLE);
  let order = spec.order_by ?? null;
  let lastResult = null;
  let display = "table";

  const dims = itemRows(spec.dimensions, { withFormat: false, kind: "dimension", kpis: pkg.kpis, max: 8 });
  const meas = itemRows(spec.measures, { withFormat: true, kind: "measure", kpis: pkg.kpis, max: 20 });
  const filter = formulaInput({ value: (spec.filters || []).join("\nAND "), rows: 2, label: null,
    placeholder: 'Optional condition, e.g. case.rework AND case.start >= "2024-01-01"' });
  const limit = h("input", { type: "number", min: 1, max: 10000, value: spec.limit || 500, style: "width:110px", "aria-label": "Row limit" });
  const result = h("div", {});
  const status = h("div", { class: "result-head" });

  const currentSpec = () => ({
    dimensions: dims.values.map(({ formula, label }) => ({ formula, label })),
    measures: meas.values,
    filters: filter.value ? [filter.value] : [],
    order_by: order,
    limit: Number(limit.value) || 500,
  });

  async function run() {
    const s = currentSpec();
    if (!s.dimensions.length && !s.measures.length) { toast("Add a dimension or a measure first.", "error"); return; }
    [...dims.rows, ...meas.rows].forEach((r) => r.f.clear());
    filter.clear();
    clear(status, h("span", { class: "muted small" }, "Running…"));
    try {
      lastResult = await api(`/api/packages/${pkg.id}/query`, { method: "POST", body: s });
      draw();
    } catch (err) {
      lastResult = null;
      clear(result);
      clear(status, h("div", { class: "notice error", style: "flex:1" }, err.message));
      if (err.code === "plql_error") {
        const m = /^(Dimension|Measure) (\d+): /.exec(err.message);
        const dimFilled = dims.rows.filter((r) => r.f.value);
        const measFilled = meas.rows.filter((r) => r.f.value);
        const target = m ? (m[1] === "Dimension" ? dimFilled : measFilled)[Number(m[2]) - 1]?.f : filter;
        target?.error({ message: err.message.replace(/^(Dimension|Measure) \d+: /, ""), position: err.position });
      }
    }
  }

  function draw() {
    const r = lastResult;
    if (!r) return;
    const dimCount = r.columns.filter((c) => c.kind === "dimension").length;
    const chartable = dimCount === 1 && r.columns.length > 1;
    if (!chartable) display = "table";
    clear(status,
      h("span", { class: "small" }, h("b", {}, fmtInt(r.total_groups)), ` row${r.total_groups === 1 ? "" : "s"}`,
        r.rows.length < r.total_groups ? ` (showing ${fmtInt(r.rows.length)})` : "",
        ` · ${fmtInt(r.case_count)} of ${fmtInt(r.total_case_count)} cases · ${r.elapsed_ms} ms`),
      h("span", { class: "grow" }),
      chartable ? h("div", { class: "chips" }, ["table", "bar", "line"].map((d) => h("button", { type: "button", class: "chip",
        "aria-pressed": String(display === d), onclick: () => { display = d; draw(); } }, d[0].toUpperCase() + d.slice(1)))) : null,
      h("button", { type: "button", class: "btn btn-sm", onclick: () => downloadText("query.csv", toCsv(r.columns, r.rows), "text/csv") }, "Download CSV"),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => addToView().catch(errorToast) }, "Add to view…"));
    if (display === "table") {
      // Sortable headers: clicking re-runs the query with that order.
      const cols = r.columns;
      clear(result, h("div", { class: "table-wrap", style: "max-height:560px" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, cols.map((c, i) => {
          const cur = order?.[0]?.[0] === i ? (order[0][1] ? " ▼" : " ▲") : "";
          return h("th", { class: c.kind === "measure" || c.type === "number" ? "num" : "", title: c.formula },
            h("button", { type: "button", class: "sort", onclick: () => {
              const desc = order?.[0]?.[0] === i ? !order[0][1] : c.kind === "measure";
              order = [[i, desc]];
              run();
            } }, c.label + cur));
        }))),
        h("tbody", {}))));
      const tbody = result.querySelector("tbody");
      const tmp = h("div"); // the table component's rows keep the formatting rules in one place
      renderComponent(tmp, { type: "table" }, r, null);
      const rows = tmp.querySelectorAll("tbody tr");
      rows.forEach((tr) => { tr.classList.remove("clickable"); tr.removeAttribute("title"); tbody.append(tr); });
      if (!r.rows.length) result.append(h("p", { class: "muted small" }, "No rows."));
    } else {
      const box = h("div", { class: "comp w12", style: "box-shadow:none" }, h("div", { class: "comp-body" }));
      clear(result, box);
      renderComponent(box.firstChild, { type: display, config: {} }, r, null);
    }
  }

  async function saveAs(asNew) {
    const s = currentSpec();
    let name = saved?.name || "";
    if (asNew || !saved) {
      const input = h("input", { value: saved ? `${saved.name} (copy)` : "", maxlength: 200, placeholder: "Query name" });
      name = await dialog({ title: "Save query", build: (b) => b.append(h("label", {}, "Name", input)),
        actions: [{ label: "Cancel" }, { label: "Save", primary: true, run: () => {
          if (!input.value.trim()) throw new Error("Give the query a name.");
          return input.value.trim();
        } }] });
      if (!name) return;
    }
    const q = asNew || !saved
      ? await api(`/api/packages/${pkg.id}/queries`, { method: "POST", body: { name, spec: s } })
      : await api(`/api/packages/${pkg.id}/queries/${saved.id}`, { method: "PUT", body: { name, spec: s } });
    await refreshPackage();
    toast("Query saved");
    if (!saved || asNew) go(`#/p/${pkg.id}/q/${q.id}`);
  }

  async function removeQuery() {
    if (!await confirmDialog("Delete query", `Delete the saved query “${saved.name}”?`)) return;
    await api(`/api/packages/${pkg.id}/queries/${saved.id}`, { method: "DELETE" });
    await refreshPackage();
    go(`#/p/${pkg.id}/q`);
  }

  async function addToView() {
    if (!pkg.views.length) { toast("Create a view first (+ next to “Views”).", "error"); return; }
    const s = currentSpec();
    const viewSel = h("select", {}, pkg.views.map((v) => h("option", { value: v.id }, v.name)));
    const typeSel = h("select", {}, h("option", { value: "table" }, "Table"),
      s.dimensions.length === 1 ? [h("option", { value: "bar" }, "Bar chart"), h("option", { value: "line" }, "Line chart")] : null);
    typeSel.value = display === "table" || s.dimensions.length !== 1 ? "table" : display;
    const title = h("input", { value: saved?.name || "", maxlength: 200 });
    const viewId = await dialog({ title: "Add to view", build: (b) => b.append(h("div", { class: "form-grid" },
      h("label", {}, "View", viewSel), h("label", {}, "Component", typeSel), h("label", { class: "span-all" }, "Title", title))),
    actions: [{ label: "Cancel" }, { label: "Add", primary: true, run: async () => {
      const v = await api(`/api/packages/${pkg.id}/views/${viewSel.value}`);
      const ids = new Set(v.components.map((c) => c.id));
      let n = v.components.length + 1;
      while (ids.has(`q${n}`)) n += 1;
      const type = typeSel.value;
      const config = type === "table"
        ? { dimensions: s.dimensions, measures: s.measures, limit: Math.min(s.limit, 200) }
        : { dimension: s.dimensions[0], measures: s.measures, limit: Math.min(s.limit, 60) };
      v.components.push({ id: `q${n}`, type, title: title.value.trim(), width: 12, filter: s.filters[0] || "", config });
      await api(`/api/packages/${pkg.id}/views/${v.id}`, { method: "PUT",
        body: { name: v.name, description: v.description, filter: v.filter, components: v.components } });
      return v.id;
    } }] });
    if (viewId) { toast("Added to the view"); await refreshPackage(); go(`#/p/${pkg.id}/v/${viewId}`); }
  }

  const filterChips = pkg.filters.length ? h("div", { class: "chips" }, h("span", { class: "small muted" }, "Insert:"),
    pkg.filters.map((f) => h("button", { type: "button", class: "chip", title: f.formula, onclick: () => {
      filter.value = filter.value ? `${filter.value} AND FILTER("${f.key}")` : `FILTER("${f.key}")`;
    } }, f.name))) : null;

  clear(canvas,
    h("div", { class: "canvas-head" },
      h("div", { class: "titles" }, h("h1", {}, saved ? saved.name : "Query workbench"),
        h("p", { class: "muted" }, "Group cases or events by dimensions and compute measures. Event dimensions (e.g. event.activity) count each case once per group.")),
      h("div", { class: "row" },
        saved ? h("button", { type: "button", class: "btn btn-danger", onclick: () => removeQuery().catch(errorToast) }, "Delete") : null,
        saved ? h("button", { type: "button", class: "btn", onclick: () => saveAs(true).catch(errorToast) }, "Save as…") : null,
        h("button", { type: "button", class: "btn", onclick: () => saveAs(false).catch(errorToast) }, saved ? "Save" : "Save query…"),
        h("button", { type: "button", class: "btn btn-primary", onclick: run, title: "⌘/Ctrl+Enter in a formula" }, "▶ Run"))),
    h("div", { class: "workbench" },
      h("div", { class: "editor card" },
        h("div", {}, h("div", { class: "section-title" }, h("h2", {}, "Dimensions"), h("span", { class: "small muted" }, "group by")), dims.el),
        h("div", {}, h("div", { class: "section-title" }, h("h2", {}, "Measures"), h("span", { class: "small muted" }, "aggregations")), meas.el),
        h("div", {}, h("div", { class: "section-title" }, h("h2", {}, "Filter"), h("span", { class: "small muted" }, "case condition")), filter.el, filterChips),
        h("div", { class: "row" }, h("label", { class: "check" }, "Max rows ", limit))),
      referencePanel(ctx.reference, { schema: ctx.schema, kpis: pkg.kpis, filters: pkg.filters })),
    h("div", { class: "card", style: "margin-top:1rem" }, h("h2", {}, "Result"), status, result));

  const onKey = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && e.target.closest?.(".editor")) { e.preventDefault(); run(); }
  };
  canvas.addEventListener("keydown", onKey);
  if (ctx.schema) run();
  return () => canvas.removeEventListener("keydown", onKey);
}
