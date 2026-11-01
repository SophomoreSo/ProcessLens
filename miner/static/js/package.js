// Package overview and package settings.
import { go, refreshPackage, reloadSchema, setCrumbs } from "./app.js";
import { api, clear, confirmDialog, downloadText, errorToast, fmtInt, fmtValue, h, toast } from "./core.js";
import { formulaInput, referencePanel } from "./formula.js";
import { datasetFields } from "./workspace.js";

function dataUnavailable(ctx) {
  return h("div", { class: "notice error" }, h("b", {}, "The data of this package is not available. "),
    ctx.schemaError?.message || "", " ", h("a", { href: `#/p/${ctx.pkg.id}/settings` }, "Open the package settings"));
}

export async function renderPackageHome(canvas, ctx) {
  const pkg = ctx.pkg;
  setCrumbs(["Overview", null]);
  const s = ctx.schema;
  const facts = s ? h("div", { class: "row small muted" },
    h("span", {}, `▣ ${s.dataset.name} (v${s.dataset.version})`),
    h("span", {}, `◎ lead object type: ${s.object_type}`),
    h("span", {}, `${fmtInt(s.case_count)} cases`), h("span", {}, `${fmtInt(s.event_count)} events`),
    h("span", {}, `${fmtInt(s.activities.length)} activities`), h("span", {}, `timezone ${s.dataset.display_timezone}`)) : null;

  const tiles = h("div", { class: "view-grid" });
  const views = h("div", { class: "card-grid" }, pkg.views.map((v) => h("a", { class: "card pkg-card", href: `#/p/${pkg.id}/v/${v.id}` },
    h("h2", {}, "▦ ", v.name), v.description ? h("p", { class: "muted small" }, v.description) : null,
    h("div", { class: "meta" }, `${v.component_count} components`))));

  clear(canvas,
    h("div", { class: "canvas-head" },
      h("div", { class: "titles" }, h("h1", {}, pkg.name), pkg.description ? h("p", { class: "muted" }, pkg.description) : null),
      h("div", { class: "row" },
        h("a", { class: "btn", href: `#/p/${pkg.id}/q` }, "▶ Query workbench"),
        h("a", { class: "btn", href: `#/p/${pkg.id}/km/kpis` }, "Σ Knowledge model"))),
    s ? facts : dataUnavailable(ctx),
    pkg.base_filter ? h("p", { class: "notice warn small" }, "Every analysis in this package only includes cases that meet the base filter ",
      h("code", {}, pkg.base_filter), ".") : null,
    h("h2", { style: "margin-top:1.2rem" }, "KPIs"), tiles,
    h("h2", { style: "margin-top:1.4rem" }, "Views"),
    pkg.views.length ? views : h("p", { class: "muted" }, "No views yet. Use + next to “Views” in the navigator to build one."));

  if (!s) return;
  if (!pkg.kpis.length) { tiles.append(h("p", { class: "muted" }, "No KPIs defined yet.")); return; }
  try {
    const res = await api(`/api/packages/${pkg.id}/kpi-values`);
    clear(tiles, pkg.kpis.map((k) => {
      const v = res.values[k.key] || {};
      return h("a", { class: "comp w3 kpi-tile", href: `#/p/${pkg.id}/km/kpis`, style: "text-decoration:none;color:inherit" },
        h("div", { class: "comp-head" }, h("h3", { title: k.formula }, k.name)),
        v.error ? h("div", { class: "comp-error" }, v.error)
          : h("div", { class: "value" }, fmtValue(v.value, v.format)),
        k.target !== null && k.target !== undefined && !v.error
          ? h("div", { class: "sub" }, `target ${fmtValue(k.target, v.format)}`) : null);
    }));
  } catch (err) {
    clear(tiles, h("div", { class: "notice error" }, err.message));
  }
}

export async function renderSettings(canvas, ctx) {
  const pkg = ctx.pkg;
  setCrumbs(["Settings", null]);
  const datasets = await api("/api/datasets");
  const name = h("input", { value: pkg.name, maxlength: 200, required: true });
  const desc = h("textarea", { rows: 3, maxlength: 4000 });
  desc.value = pkg.description || "";
  const fields = datasets.length ? datasetFields(datasets, { withStarter: false, current: pkg }) : null;
  const base = formulaInput({ value: pkg.base_filter || "", rows: 2, label: "Base filter (optional PLQL condition)",
    placeholder: 'e.g. case.start >= "2024-01-01" AND NOT CONTAINS("Cancel order")' });

  const save = async () => {
    try {
      await api(`/api/packages/${pkg.id}`, { method: "PATCH", body: {
        name: name.value.trim(), description: desc.value, base_filter: base.value,
        ...(fields ? { dataset_id: fields.dataset_id, object_type: fields.object_type } : {}) } });
      await refreshPackage();
      await reloadSchema();
      toast("Package saved");
      go(`#/p/${pkg.id}/settings`);
    } catch (err) {
      if (err.code === "plql_error") base.error(err); else errorToast(err);
    }
  };
  const test = async () => {
    if (!base.value) { base.ok("No base filter: every case is included."); return; }
    try {
      const r = await api(`/api/packages/${pkg.id}/evaluate`, { method: "POST", body: { formula: base.value, kind: "condition" } });
      base.ok(`Keeps ${fmtInt(r.matching)} of ${fmtInt(r.total)} cases.`);
    } catch (err) { base.error(err); }
  };
  const exportPkg = async () => {
    const doc = await api(`/api/packages/${pkg.id}/export`);
    const slug = pkg.name.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "package";
    downloadText(`${slug}.miner.json`, JSON.stringify(doc, null, 2), "application/json");
  };
  const del = async () => {
    if (!await confirmDialog("Delete package", `Delete “${pkg.name}” with its KPIs, filters, views and queries? ` +
      "The ProcessLens dataset is not affected. This cannot be undone.")) return;
    await api(`/api/packages/${pkg.id}`, { method: "DELETE" });
    toast("Package deleted");
    go("#/");
  };

  clear(canvas,
    h("div", { class: "canvas-head" }, h("div", { class: "titles" }, h("h1", {}, "Package settings"))),
    h("div", { class: "card stack" },
      h("div", { class: "form-grid" },
        h("label", { class: "span-all" }, "Name", name),
        h("label", { class: "span-all" }, "Description", desc),
        fields ? fields.els : h("p", { class: "notice error span-all" }, "ProcessLens has no ready datasets.")),
      h("div", { class: "dialog-split" },
        h("div", {}, base.el,
          h("p", { class: "small muted" }, "Applies to every KPI, view and query of the package. Event conditions keep the cases with at least one matching event."),
          h("button", { type: "button", class: "btn btn-sm", onclick: test }, "Test base filter")),
        referencePanel(ctx.reference, { schema: ctx.schema, kpis: pkg.kpis, filters: pkg.filters })),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn btn-primary", onclick: save }, "Save changes"))),
    h("div", { class: "card stack", style: "margin-top:1rem" },
      h("h2", {}, "Share and remove"),
      h("p", { class: "muted small" }, "Export the package (knowledge model, views, queries — no event data) to import it elsewhere."),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", onclick: () => exportPkg().catch(errorToast) }, "Export package"),
        h("button", { type: "button", class: "btn btn-danger", onclick: () => del().catch(errorToast) }, "Delete package"))));
}
