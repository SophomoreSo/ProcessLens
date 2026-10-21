// Parameter forms generated from the algorithm registry (only parameters the
// implementation supports; defaults and help texts come from the registry).
import { badge, clear, h, t } from "./core.js";

export async function loadRegistry(api) {
  return (await api("/api/mining/algorithms")).algorithms;
}

export function algorithmOptions(select, registry, selected) {
  const families = [...new Set(registry.map((a) => a.family))];
  clear(select, families.map((fam) => h("optgroup", { label: t(`algo.family.${fam}`) },
    registry.filter((a) => a.family === fam).map((a) => h("option", {
      value: a.id, disabled: a.availability.status !== "available", selected: a.id === selected,
    }, `${t(a.name_key)}${a.availability.status !== "available" ? ` — ${t("algo.unavailable_short")}` : ""}`)))));
}

export function algorithmInfo(spec) {
  const av = spec.availability;
  return h("div", {},
    h("div", { class: "chips" },
      av.status === "available" ? badge(t("algo.available"), "ok") : badge(t("algo.unavailable_short"), "warn"),
      badge(`${t("algo.output")}: ${t(`model.type.${spec.output_type}`)}`),
      spec.derived_representations.map((r) => badge(`${t("algo.derived")}: ${t(`model.type.${r}`)}`)),
      spec.deterministic ? null : badge(t("algo.randomized"), "warn"),
      badge(`${spec.implementation.provider} ${spec.implementation.provider_version || ""}`)),
    av.status !== "available" ? h("p", { class: "notice warn small" }, t(av.reason_key, av.reason_params || {})) : null,
    spec.preconditions.length ? h("div", {}, h("b", {}, t("algo.preconditions")), h("ul", {}, spec.preconditions.map((k) => h("li", {}, t(k))))) : null,
    spec.limitations.length ? h("div", {}, h("b", {}, t("algo.limitations")), h("ul", {}, spec.limitations.map((k) => h("li", {}, t(k))))) : null,
    h("p", { class: "muted" }, t("algo.evaluations"), " ",
      spec.evaluations.length ? spec.evaluations.map((m) => t(`method.${m}`)).join(", ") : t("algo.no_evaluations")),
    h("p", { class: "muted" }, t("algo.exports"), " ", spec.exports.map((e) => e.toUpperCase()).join(", ")));
}

/** Build inputs for a spec's parameters inside ``form``. ``activities`` feeds activity pickers. */
export function paramInputs(form, spec, activities = [], values = {}) {
  const fields = spec.params.map((p) => {
    const v = values[p.name] ?? p.default;
    let input;
    if (p.type === "bool") {
      input = h("input", { type: "checkbox", name: p.name, checked: Boolean(v) });
      return h("label", { class: "inline", title: t(p.help_key) }, input, h("span", {}, p.name));
    }
    if (p.type === "choice") {
      input = h("select", { name: p.name }, p.choices.map((c) => h("option", { value: c, selected: c === v }, c)));
    } else if (p.type === "multichoice" || p.type === "activities") {
      const options = p.type === "activities" ? activities : p.choices;
      input = h("select", { name: p.name, multiple: true }, options.map((c) => h("option", { value: c, selected: (v || []).includes(c) }, c)));
    } else {
      input = h("input", { type: "number", name: p.name, step: p.type === "int" ? "1" : "any",
        min: p.minimum ?? "", max: p.maximum ?? "", value: v ?? "", placeholder: v === null ? t("algo.optional") : "" });
    }
    return h("label", {}, h("span", {}, p.name), input, h("span", { class: "small muted" }, t(p.help_key)));
  });
  clear(form, fields.length ? fields : h("p", { class: "small muted" }, t("algo.no_params")));
}

export function readParams(form, spec) {
  const out = {};
  for (const p of spec.params) {
    const el = form.elements[p.name];
    if (!el) continue;
    if (p.type === "bool") out[p.name] = el.checked;
    else if (p.type === "multichoice" || p.type === "activities") out[p.name] = [...el.selectedOptions].map((o) => o.value);
    else if (p.type === "choice") out[p.name] = el.value;
    else if (el.value !== "") out[p.name] = Number(el.value);
  }
  return out;
}
