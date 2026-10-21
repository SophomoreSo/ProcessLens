import { api, clear, h, t } from "./core.js";

/** Poll an import job until it finishes, rendering progress into ``el``. */
export async function followJob(jobId, el) {
  const bar = h("i", { style: { width: "0%" } });
  const stage = h("span", {}, t("job.queued"));
  clear(el, h("div", { class: "notice info" }, h("div", { class: "actions" }, h("span", { class: "spinner" }), stage),
    h("div", { class: "progress", style: { marginTop: "8px" }, role: "progressbar" }, bar)));
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    bar.style.width = `${Math.round(job.progress * 100)}%`;
    stage.textContent = `${job.stage} (${Math.round(job.progress * 100)}%)`;
    if (["succeeded", "failed", "interrupted"].includes(job.status)) return job;
    await new Promise((r) => setTimeout(r, 400));
  }
}
