// ProcessLens Miner single-page studio: hash routes, the package navigator and page dispatch.
import { $, api, clear, h, initTheme } from "./core.js";
import { loadReference } from "./formula.js";
import { renderKnowledge } from "./knowledge.js";
import { renderPackageHome, renderSettings } from "./package.js";
import { renderQuery } from "./query.js";
import { renderView } from "./view.js";
import { renderWorkspace } from "./workspace.js";

/** Shared state of the open package. */
export const ctx = { pkg: null, schema: null, reference: null, schemaError: null };

let cleanup = null;
let leaveGuard = null;
let currentHash = location.hash;

/** Pages with unsaved changes register a guard; it is asked before navigating away. */
export function setLeaveGuard(fn) { leaveGuard = fn; }

export function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

export async function refreshPackage() {
  if (!ctx.pkg) return;
  ctx.pkg = await api(`/api/packages/${ctx.pkg.id}`);
  renderNavigator();
}

async function openPackage(id) {
  if (ctx.pkg?.id === id) return;
  ctx.pkg = await api(`/api/packages/${id}`);
  ctx.schema = null;
  ctx.schemaError = null;
  try {
    ctx.schema = await api(`/api/packages/${id}/schema`);
  } catch (err) {
    ctx.schemaError = err;
  }
}

export async function reloadSchema() {
  ctx.schemaError = null;
  try { ctx.schema = await api(`/api/packages/${ctx.pkg.id}/schema`); } catch (err) { ctx.schema = null; ctx.schemaError = err; }
}

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] !== "p" || !parts[1]) return { page: "workspace" };
  const id = Number(parts[1]);
  const sub = parts[2];
  if (!sub) return { page: "home", id };
  if (sub === "km") return { page: "knowledge", id, tab: parts[3] || "kpis" };
  if (sub === "v" && parts[3]) return { page: "view", id, viewId: Number(parts[3]) };
  if (sub === "q") return { page: "query", id, queryId: parts[3] ? Number(parts[3]) : null };
  if (sub === "settings") return { page: "settings", id };
  return { page: "home", id };
}

function crumbs(items) {
  const nav = $("#crumbs");
  clear(nav, items.map(([label, href], i) => [
    i ? h("span", { class: "sep", "aria-hidden": "true" }, "›") : null,
    href ? h("a", { href }, label) : h("span", { "aria-current": "page" }, label),
  ]));
}

export function setCrumbs(...items) {
  const base = [["Workspace", "#/"]];
  if (ctx.pkg) base.push([ctx.pkg.name, `#/p/${ctx.pkg.id}`]);
  crumbs([...base, ...items]);
}

function navLink(href, icon, label, current) {
  return h("li", {}, h("a", { href, "aria-current": current ? "page" : null },
    h("span", { class: "ico", "aria-hidden": "true" }, icon), h("span", { class: "lbl" }, label)));
}

export function renderNavigator() {
  const nav = $("#navigator");
  const pkg = ctx.pkg;
  const r = parseRoute();
  if (!pkg || r.page === "workspace") { nav.classList.add("hidden"); return; }
  nav.classList.remove("hidden");
  const here = location.hash;
  clear(nav,
    h("div", { class: "nav-pkg" },
      h("b", {}, pkg.name),
      h("div", { class: "small muted" }, `${pkg.dataset_name ?? `dataset #${pkg.dataset_id}`} · ${pkg.object_type ?? ""}`),
      pkg.base_filter ? h("div", { class: "small" }, h("span", { class: "badge warn", title: pkg.base_filter }, "base filter")) : null),
    h("ul", { class: "nav-list" }, navLink(`#/p/${pkg.id}`, "⌂", "Overview", r.page === "home")),
    h("div", { class: "nav-section" },
      h("div", { class: "nav-section-head" }, "Knowledge model"),
      h("ul", { class: "nav-list" },
        navLink(`#/p/${pkg.id}/km/kpis`, "Σ", `KPIs (${pkg.kpis.length})`, r.page === "knowledge" && r.tab === "kpis"),
        navLink(`#/p/${pkg.id}/km/filters`, "⧩", `Filters (${pkg.filters.length})`, r.page === "knowledge" && r.tab === "filters"))),
    h("div", { class: "nav-section" },
      h("div", { class: "nav-section-head" }, "Views",
        h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "New view", "aria-label": "New view",
          onclick: newView }, "+")),
      h("ul", { class: "nav-list" }, pkg.views.map((v) => navLink(`#/p/${pkg.id}/v/${v.id}`, "▦", v.name,
        here === `#/p/${pkg.id}/v/${v.id}` || here.startsWith(`#/p/${pkg.id}/v/${v.id}/`))))),
    h("div", { class: "nav-section" },
      h("div", { class: "nav-section-head" }, "Queries"),
      h("ul", { class: "nav-list" },
        navLink(`#/p/${pkg.id}/q`, "▶", "Query workbench", r.page === "query" && !r.queryId),
        pkg.queries.map((q) => navLink(`#/p/${pkg.id}/q/${q.id}`, "≡", q.name, r.page === "query" && r.queryId === q.id)))),
    h("div", { class: "nav-section" },
      h("div", { class: "nav-section-head" }, "Package"),
      h("ul", { class: "nav-list" }, navLink(`#/p/${pkg.id}/settings`, "⚙", "Settings", r.page === "settings"))),
  );
}

async function newView() {
  const pkg = ctx.pkg;
  const v = await api(`/api/packages/${pkg.id}/views`, { method: "POST",
    body: { name: `View ${pkg.views.length + 1}`, components: [] } });
  await refreshPackage();
  go(`#/p/${pkg.id}/v/${v.id}?edit`);
}

async function route() {
  if (leaveGuard && location.hash !== currentHash && !leaveGuard()) {
    history.replaceState(null, "", currentHash || "#/");
    return;
  }
  leaveGuard = null;
  currentHash = location.hash;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const canvas = $("#canvas");
  const r = parseRoute();
  try {
    if (r.page === "workspace") {
      ctx.pkg = null;
      renderNavigator();
      crumbs([["Workspace", null]]);
      cleanup = await renderWorkspace(canvas);
      return;
    }
    await openPackage(r.id);
    ctx.reference = ctx.reference || await loadReference();
    renderNavigator();
    const pages = {
      home: () => renderPackageHome(canvas, ctx),
      knowledge: () => renderKnowledge(canvas, ctx, r.tab),
      view: () => renderView(canvas, ctx, r.viewId, location.hash.includes("?edit")),
      query: () => renderQuery(canvas, ctx, r.queryId),
      settings: () => renderSettings(canvas, ctx),
    };
    cleanup = await pages[r.page]();
  } catch (err) {
    ctx.pkg = err.status === 404 ? null : ctx.pkg;
    renderNavigator();
    clear(canvas, h("div", { class: "notice error" }, h("b", {}, "Could not open this page. "), err.message),
      h("p", {}, h("a", { href: "#/" }, "Back to the workspace")));
  }
  canvas.focus({ preventScroll: true });
}

window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (e) => { if (leaveGuard && !leaveGuard(true)) e.preventDefault(); });
initTheme();
route();
