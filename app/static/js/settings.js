import { $, $$, api, bootPage, IS_ADMIN, motionEnabled, t, toast } from "./core.js";

/** Guests keep their choice in this browser only (the guest site never writes preferences). */
function saveGuestCookies(language, motion) {
  const opts = "; path=/; max-age=31536000; SameSite=Lax";
  document.cookie = `processlens_guest_lang=${encodeURIComponent(language)}${opts}`;
  document.cookie = `processlens_guest_motion=${encodeURIComponent(motion)}${opts}`;
}

const root = document.documentElement;
const saved = { motion: root.dataset.motion || "system" };
const reduced = matchMedia("(prefers-reduced-motion: reduce)");

function describeState() {
  $("#os-motion").textContent = reduced.matches ? t("set.os_reduced") : t("set.os_normal");
  $("#motion-state").textContent = motionEnabled() ? t("set.preview_on") : t("set.preview_off");
}

// Preview the animation choice immediately (it is only stored when saved).
for (const input of $$('input[name="motion"]')) {
  input.addEventListener("change", () => {
    root.dataset.motion = input.value;
    root.dataset.motionActive = String(motionEnabled());
    describeState();
  });
}
reduced.addEventListener("change", describeState);

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const language = $('input[name="language"]:checked')?.value;
  const motion = $('input[name="motion"]:checked')?.value;
  $("#save-settings").disabled = true;
  try {
    if (IS_ADMIN) await api("/api/preferences", { method: "PUT", body: { language, motion } });
    else saveGuestCookies(language, motion);
    saved.motion = motion;
    if (language !== root.lang) {
      location.reload(); // the page is rendered server-side in the selected language
      return;
    }
    toast(t("set.saved"));
  } catch (err) {
    toast(err.message, "error");
  } finally {
    $("#save-settings").disabled = false;
  }
});

window.addEventListener("beforeunload", () => { root.dataset.motion = saved.motion; });
bootPage(describeState, { filters: false });
