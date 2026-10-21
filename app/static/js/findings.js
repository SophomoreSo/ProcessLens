import { badge, h, SEVERITY_LABEL, t, table } from "./core.js";

/** Table of validation findings (import wizard and dataset findings). */
export function findingsTable(findings) {
  return table([
    { key: "severity", label: t("finding.col.severity"), render: (f) => badge(SEVERITY_LABEL[f.severity] || f.severity, f.severity) },
    { key: "code", label: t("finding.col.code"), render: (f) => h("code", {}, f.code) },
    { key: "row", label: t("finding.col.row"), num: true, render: (f) => f.row ?? "—" },
    { key: "field", label: t("finding.col.column"), render: (f) => f.field ?? "—" },
    { key: "case_id", label: t("finding.col.case"), render: (f) => f.case_id ?? "—" },
    { key: "value", label: t("finding.col.value"), render: (f) => f.value ?? "—" },
    { key: "message", label: t("finding.col.message") },
  ], findings);
}
