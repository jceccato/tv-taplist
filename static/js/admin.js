/* Admin UI behaviour: AJAX form saves, override toggles, manual sync. */
(() => {
  "use strict";

  const toast = document.getElementById("toast");
  let toastTimer = null;

  function showToast(msg, kind = "ok") {
    toast.textContent = msg;
    toast.className = "toast " + kind;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
  }

  async function postForm(url, formData) {
    const resp = await fetch(url, { method: "POST", body: formData });
    let body = null;
    try { body = await resp.json(); } catch (_) { /* non-JSON */ }
    if (!resp.ok) {
      const detail = (body && body.detail) || ("HTTP " + resp.status);
      throw new Error(detail);
    }
    return body;
  }

  // ---- settings form ----
  const settingsForm = document.getElementById("settings-form");
  if (settingsForm) {
    // Keep the logo-height slider and number input in sync.
    const hRange = document.getElementById("logo_h_range");
    const hNum = document.getElementById("logo_h");
    if (hRange && hNum) {
      hRange.addEventListener("input", () => { hNum.value = hRange.value; });
      hNum.addEventListener("input", () => { hRange.value = hNum.value; });
    }

    settingsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(settingsForm);
      // Unchecked checkboxes are omitted from FormData; normalise every one to
      // an explicit bool so the server always gets the intended value. Iterating
      // form.elements also covers controls associated via the form= attribute
      // (the Theme tab and the venue-logo height live outside the <form>).
      Array.from(settingsForm.elements).forEach((cb) => {
        if (cb.type === "checkbox" && cb.name) fd.set(cb.name, cb.checked ? "true" : "false");
      });
      try {
        await postForm("/admin/settings", fd);
        showToast("Settings saved. Reloading…", "ok");
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        showToast("Save failed: " + err.message, "err");
      }
    });
  }

  // ---- venue logo ----
  const venueForm = document.getElementById("venue-logo-form");
  if (venueForm) {
    venueForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(venueForm);
      if (!fd.get("image") || !fd.get("image").name) {
        showToast("Choose an image file first.", "err");
        return;
      }
      try {
        await postForm("/admin/venue-logo", fd);
        showToast("Venue logo uploaded. Reloading…", "ok");
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        showToast("Upload failed: " + err.message, "err");
      }
    });

    const removeBtn = document.getElementById("remove-logo");
    if (removeBtn) {
      removeBtn.addEventListener("click", async () => {
        const fd = new FormData();
        fd.set("remove", "true");
        try {
          await postForm("/admin/venue-logo", fd);
          showToast("Venue logo removed. Reloading…", "ok");
          setTimeout(() => location.reload(), 700);
        } catch (err) {
          showToast("Remove failed: " + err.message, "err");
        }
      });
    }
  }

  // ---- manual override rows ----
  document.querySelectorAll(".override-row").forEach((row) => {
    const toggle = row.querySelector(".override-toggle");
    const fields = row.querySelector(".override-fields");

    toggle.addEventListener("change", () => {
      fields.hidden = !toggle.checked;
      if (!toggle.checked) {
        // Immediately release the slot back to Brewfather control.
        submitOverride(row, false);
      }
    });

    row.addEventListener("submit", (e) => {
      e.preventDefault();
      submitOverride(row, true);
    });
  });

  async function submitOverride(row, enabled) {
    const tap = row.dataset.tap;
    const fd = new FormData(row);
    fd.set("enabled", enabled ? "true" : "false");
    try {
      await postForm(`/admin/override/${tap}`, fd);
      showToast(enabled ? `Tap ${tap} override saved.` : `Tap ${tap} released to Brewfather.`, "ok");
      const tag = row.querySelector(".source-tag");
      if (tag) tag.textContent = enabled ? "custom" : "vacant";
    } catch (err) {
      showToast(`Tap ${tap} failed: ` + err.message, "err");
    }
  }

  // ---- tabs (Settings | Theme | Manual overrides) ----
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".tab-panel");
  function activateTab(name) {
    let matched = false;
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      matched = matched || on;
    });
    if (!matched) return;
    panels.forEach((p) => { p.hidden = (p.id !== "tab-" + name); });
    try { localStorage.setItem("admin_tab", name); } catch (_) { /* private mode */ }
  }
  tabs.forEach((t) => t.addEventListener("click", () => activateTab(t.dataset.tab)));
  // Restore the last tab, so a settings save (which reloads) returns you to it.
  try {
    const saved = localStorage.getItem("admin_tab");
    if (saved) activateTab(saved);
  } catch (_) { /* ignore */ }

  // ---- theme preset: live hint + dim the custom colours unless "Custom" ----
  const themeSelect = document.getElementById("theme");
  const themeHint = document.getElementById("theme-hint");
  const customTheme = document.getElementById("custom-theme");
  function syncTheme() {
    if (!themeSelect) return;
    const opt = themeSelect.options[themeSelect.selectedIndex];
    if (themeHint && opt) themeHint.textContent = opt.dataset.hint || "";
    if (customTheme) customTheme.classList.toggle("dim", themeSelect.value !== "custom");
  }
  if (themeSelect) { themeSelect.addEventListener("change", syncTheme); syncTheme(); }

  // ---- manual sync ----
  const syncBtn = document.getElementById("sync-now");
  if (syncBtn) {
    syncBtn.addEventListener("click", async () => {
      syncBtn.disabled = true;
      const original = syncBtn.textContent;
      syncBtn.textContent = "Syncing…";
      try {
        const res = await postForm("/admin/sync", new FormData());
        if (res && res.ok) {
          showToast(`Sync OK — ${res.written} written, ${res.archived} archived.`, "ok");
          if (res.timestamp) {
            const el = document.getElementById("status-last-sync");
            if (el) el.textContent = res.timestamp;
            const errEl = document.getElementById("status-last-error");
            if (errEl) { errEl.textContent = "none"; errEl.classList.remove("err"); }
          }
        } else if (res && res.skipped) {
          showToast("Sync skipped: " + (res.message || "no credentials"), "err");
        } else {
          showToast("Sync failed: " + ((res && res.message) || "unknown"), "err");
        }
      } catch (err) {
        showToast("Sync failed: " + err.message, "err");
      } finally {
        syncBtn.disabled = false;
        syncBtn.textContent = original;
      }
    });
  }
})();
