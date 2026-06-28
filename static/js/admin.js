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
    settingsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(settingsForm);
      // Unchecked checkboxes are omitted from FormData; normalise to a bool.
      fd.set("hide_vacant_taps", settingsForm.querySelector('[name=hide_vacant_taps]').checked ? "true" : "false");
      try {
        await postForm("/admin/settings", fd);
        showToast("Settings saved. Reloading…", "ok");
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        showToast("Save failed: " + err.message, "err");
      }
    });
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
