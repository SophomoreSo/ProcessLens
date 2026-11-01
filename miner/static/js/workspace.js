// Workspace: the list of packages, creating a package and importing a package file.
import { go } from "./app.js";
import { api, clear, dialog, errorToast, fmtInt, h, toast } from "./core.js";

function datasetFields(datasets, { withStarter = true, current = null } = {}) {
  const dsSelect = h("select", { name: "dataset_id", required: true },
    datasets.map((d) => h("option", { value: d.id }, `${d.name} (#${d.id}, v${d.version})`)));
  const otSelect = h("select", { name: "object_type" });
  const fillTypes = () => {
    const d = datasets.find((x) => String(x.id) === dsSelect.value);
    clear(otSelect, (d?.object_types || []).map((t) => h("option", { value: t },
      t === d.main_object_type ? `${t} (main)` : t)));
    if (d) otSelect.value = current?.object_type && d.object_types.includes(current.object_type) ? current.object_type : d.main_object_type;
  };
  if (current?.dataset_id) dsSelect.value = String(current.dataset_id);
  dsSelect.addEventListener("change", fillTypes);
  fillTypes();
  const starter = h("input", { type: "checkbox", name: "starter", checked: true });
  return {
    els: [h("label", {}, "ProcessLens dataset", dsSelect),
      h("label", { title: "Objects of this type are the cases of every analysis in the package" }, "Lead object type", otSelect),
      withStarter ? h("label", { class: "check span-all" }, starter,
        "Start with a knowledge model and two views (KPIs, filters, process overview, throughput analysis)") : null],
    get dataset_id() { return Number(dsSelect.value); },
    get object_type() { return otSelect.value || null; },
    get starter() { return starter.checked; },
  };
}

let opening = false;

export async function newPackageDialog() {
  if (opening) return;
  opening = true;
  let datasets;
  try { datasets = await api("/api/datasets"); } finally { opening = false; }
  if (!datasets.length) {
    toast("There are no ready datasets in ProcessLens yet. Import one (or load the demo data) first.", "error");
    return;
  }
  let name;
  let desc;
  let fields;
  const created = await dialog({
    title: "New package",
    build: (body) => {
      name = h("input", { name: "name", required: true, maxlength: 200, placeholder: "e.g. Purchase-to-pay" });
      desc = h("textarea", { name: "description", rows: 2, maxlength: 4000 });
      fields = datasetFields(datasets);
      body.append(h("div", { class: "form-grid" },
        h("label", { class: "span-all" }, "Name", name),
        h("label", { class: "span-all" }, "Description", desc),
        fields.els));
    },
    actions: [{ label: "Cancel" }, { label: "Create package", primary: true, run: async () => {
      if (!name.value.trim()) { name.focus(); throw new Error("Give the package a name."); }
      return api("/api/packages", { method: "POST", body: { name: name.value.trim(), description: desc.value,
        dataset_id: fields.dataset_id, object_type: fields.object_type, starter_content: fields.starter } });
    } }],
  });
  if (created) {
    toast(`Package “${created.name}” created`);
    go(created.views.length ? `#/p/${created.id}/v/${created.views[0].id}` : `#/p/${created.id}`);
  }
}

async function importDialog() {
  if (opening) return;
  opening = true;
  let datasets;
  try { datasets = await api("/api/datasets"); } finally { opening = false; }
  if (!datasets.length) { toast("There are no ready datasets in ProcessLens yet.", "error"); return; }
  let file;
  let fields;
  const created = await dialog({
    title: "Import package",
    build: (body) => {
      file = h("input", { type: "file", accept: ".json,application/json", required: true });
      fields = datasetFields(datasets, { withStarter: false });
      body.append(h("p", { class: "muted small" },
        "A package file (.miner.json) holds the knowledge model, views and saved queries — no event data. ",
        "Choose the dataset it should analyse."),
        h("div", { class: "form-grid" }, h("label", { class: "span-all" }, "Package file", file), fields.els));
    },
    actions: [{ label: "Cancel" }, { label: "Import", primary: true, run: async () => {
      const f = file.files?.[0];
      if (!f) throw new Error("Choose a package file.");
      if (f.size > 5 * 1024 * 1024) throw new Error("Package files are at most 5 MB.");
      let doc;
      try { doc = JSON.parse(await f.text()); } catch { throw new Error("The file is not valid JSON."); }
      return api("/api/packages/import", { method: "POST",
        body: { document: doc, dataset_id: fields.dataset_id, object_type: fields.object_type } });
    } }],
  });
  if (created) { toast(`Imported “${created.name}”`); go(`#/p/${created.id}`); }
}

export { datasetFields };

export async function renderWorkspace(canvas) {
  const [packages, datasets] = await Promise.all([api("/api/packages"), api("/api/datasets")]);
  const head = h("div", { class: "canvas-head" },
    h("div", { class: "titles" }, h("h1", {}, "Workspace"),
      h("p", { class: "muted" }, "Packages bundle a knowledge model (KPIs and filters), dashboards and queries for one ProcessLens dataset.")),
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn", onclick: () => importDialog().catch(errorToast) }, "Import package"),
      h("button", { type: "button", class: "btn btn-primary", onclick: () => newPackageDialog().catch(errorToast) }, "+ New package")));

  let body;
  if (!packages.length) {
    body = h("div", { class: "card empty" },
      h("h2", {}, "Build your first analysis"),
      datasets.length
        ? h("p", {}, `ProcessLens has ${datasets.length} dataset${datasets.length > 1 ? "s" : ""} ready. `,
          "Create a package to get a starter knowledge model and dashboards you can change.")
        : h("p", {}, "ProcessLens has no datasets yet. Open ProcessLens, import an event log or load the demo data, then come back."),
      h("div", { class: "row", style: "justify-content:center" },
        datasets.length
          ? h("button", { type: "button", class: "btn btn-primary", onclick: () => newPackageDialog().catch(errorToast) }, "+ New package")
          : h("a", { class: "btn btn-primary", href: document.body.dataset.processlensUrl, target: "_blank", rel: "noopener" }, "Open ProcessLens ↗")));
  } else {
    body = h("div", { class: "card-grid" }, packages.map((p) => h("a", { class: "card pkg-card", href: `#/p/${p.id}` },
      h("h2", {}, p.name),
      p.description ? h("p", { class: "muted small" }, p.description) : null,
      h("div", { class: "meta" },
        h("span", {}, "▣ ", p.dataset_name ?? `dataset #${p.dataset_id}`),
        h("span", {}, "◎ ", p.object_type ?? "—"),
        p.dataset_ready ? null : h("span", { class: "badge danger" }, "dataset missing")),
      h("div", { class: "meta" },
        h("span", {}, `${fmtInt(p.counts.kpis)} KPIs`), h("span", {}, `${fmtInt(p.counts.filters)} filters`),
        h("span", {}, `${fmtInt(p.counts.views)} views`), h("span", {}, `${fmtInt(p.counts.queries)} queries`)))));
  }
  clear(canvas, head, body);
}
