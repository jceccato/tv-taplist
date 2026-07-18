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

  // ---- shared hex colour field ----
  // Mirror app/colors.py parse_hex_color so the client and server never disagree:
  // accept #rrggbb / rrggbb / #rgb / rgb; return normalised "#rrggbb" or null.
  function normalizeHex(value) {
    if (typeof value !== "string") return null;
    const m = /^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/.exec(value.trim());
    if (!m) return null;
    let h = m[1];
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    return "#" + h.toLowerCase();
  }

  // Wire a native <input type="color"> swatch to an adjacent hex text input so
  // they stay two-way in sync. Which one carries the submitted `name` differs by
  // use (theme = the swatch, override = the text); the sync is symmetric either
  // way. `data-allow-empty` on the text means blank is valid (e.g. "no override").
  function wireColorField(container) {
    const swatch = container.querySelector('input[type="color"]');
    const text = container.querySelector("[data-hex-text]");
    if (!swatch || !text) return;
    const allowEmpty = text.hasAttribute("data-allow-empty");

    function setInvalid(on) {
      text.classList.toggle("invalid", on);
      if (on) text.setAttribute("aria-invalid", "true");
      else text.removeAttribute("aria-invalid");
    }

    // Typing a valid hex updates the swatch; invalid text marks the field and
    // leaves the swatch unchanged (last valid colour). Blank clears when allowed.
    text.addEventListener("input", () => {
      const raw = text.value.trim();
      if (raw === "" && allowEmpty) { setInvalid(false); return; }
      const hex = normalizeHex(raw);
      if (hex) { swatch.value = hex; setInvalid(false); }
      else { setInvalid(true); }
    });

    // Picking in the swatch writes a normalised #rrggbb back into the text box.
    // Re-dispatch `input` on the text so downstream listeners (the live preview
    // and token block) update as if the operator had typed it.
    swatch.addEventListener("input", () => {
      text.value = swatch.value;
      setInvalid(false);
      text.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const clearBtn = container.querySelector("[data-hex-clear]");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        text.value = "";
        setInvalid(false);
        text.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }
  }
  document.querySelectorAll(".color-field").forEach(wireColorField);

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

    setupOverrideDynamic(row);
  });

  // Live colour preview + Brewfather token block for one override row. Both react
  // to the colour-override / colour / saturation / glass fields as they change.
  function setupOverrideDynamic(row) {
    const tap = row.dataset.tap;
    const colorInput = row.querySelector('input[name="color"]');
    const satInput = row.querySelector('input[name="saturation"]');
    const overrideInput = row.querySelector('input[name="color_override"]');
    const glassSelect = row.querySelector('select[name="glass"]');
    const indicator = row.querySelector("[data-color-indicator]");
    const tokenBox = row.querySelector("[data-token-block]");

    // ---- Feature 3: live colour indicator (server computes it, one source) ----
    let previewTimer = null;
    let previewSeq = 0;  // guards against a slow older fetch painting over a newer one
    async function refreshIndicator() {
      if (!indicator) return;
      const params = new URLSearchParams();
      const color = colorInput ? colorInput.value.trim() : "";
      const sat = satInput ? satInput.value.trim() : "";
      const override = overrideInput ? overrideInput.value.trim() : "";
      // `color` is in the admin's display unit; the server converts SRM->EBC.
      if (color !== "") params.set("ebc", color);
      if (sat !== "") params.set("sat", sat);
      if (override !== "") params.set("hex", override);
      const seq = ++previewSeq;
      try {
        const resp = await fetch("/api/preview-color?" + params.toString());
        if (!resp.ok || seq !== previewSeq) return;  // superseded by a newer edit
        const body = await resp.json();
        if (seq !== previewSeq) return;
        indicator.style.background = body.color_hex;
        indicator.style.borderColor = body.color_hex;
      } catch (_) { /* offline: leave the last colour */ }
    }
    function scheduleIndicator() {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(refreshIndicator, 150);
    }

    // ---- Feature 4: Brewfather token block (only set/non-default tokens) ----
    function buildTokens() {
      const lines = ["tap:" + tap];  // always included
      const override = overrideInput ? normalizeHex(overrideInput.value.trim()) : null;
      if (override) lines.push("colour:" + override);
      const glass = glassSelect ? glassSelect.value.trim() : "";
      if (glass) lines.push("glass:" + glass);
      const satRaw = satInput ? satInput.value.trim() : "";
      if (satRaw !== "") {
        const n = Math.round(parseFloat(satRaw));
        if (!Number.isNaN(n)) lines.push("saturation:" + n);
      }
      return lines.join("\n");
    }
    function refreshTokens() {
      if (tokenBox) tokenBox.value = buildTokens();
    }

    [colorInput, satInput, overrideInput].forEach((el) => {
      if (el) el.addEventListener("input", () => { scheduleIndicator(); refreshTokens(); });
    });
    if (glassSelect) glassSelect.addEventListener("change", refreshTokens);

    refreshIndicator();  // initial paint
    refreshTokens();
  }

  // ---- copy the token block (Clipboard API, with an execCommand fallback) ----
  async function copyText(box) {
    const text = box.value;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall through */ }
    }
    // Fallback for older browsers / non-secure (HTTP) contexts.
    try {
      box.removeAttribute("readonly");
      box.select();
      const ok = document.execCommand("copy");
      box.setAttribute("readonly", "");
      window.getSelection().removeAllRanges();
      return ok;
    } catch (_) {
      box.setAttribute("readonly", "");
      return false;
    }
  }
  document.querySelectorAll("[data-token-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const box = btn.closest(".override-fields").querySelector("[data-token-block]");
      if (!box) return;
      const ok = await copyText(box);
      showToast(ok ? "Brewfather tokens copied." : "Copy failed - select and copy manually.",
                ok ? "ok" : "err");
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
          showToast(`Sync OK - ${res.written} written, ${res.archived} archived.`, "ok");
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

  // ---- update check ----
  const updateAvailable = document.getElementById("status-update-available");
  const updateLink = document.getElementById("status-update-link");
  const latestVersion = document.getElementById("status-latest-version");
  const versionEl = document.getElementById("status-version");

  async function refreshUpdateStatus() {
    try {
      const resp = await fetch("/api/update-status");
      if (!resp.ok) return;
      const data = await resp.json();
      if (versionEl) versionEl.textContent = data.current_version || "dev";
      if (data.update_available && updateAvailable && updateLink && latestVersion) {
        updateAvailable.hidden = false;
        updateLink.textContent = "Update available";
        updateLink.href = data.latest_url || "#";
        latestVersion.textContent = data.latest_version;
      } else if (updateAvailable) {
        updateAvailable.hidden = true;
      }
    } catch (_) { /* offline: leave the last state */ }
  }

  // Poll on load and every 5 minutes (lightweight, no auth needed).
  refreshUpdateStatus();
  setInterval(refreshUpdateStatus, 5 * 60 * 1000);

  const checkUpdateBtn = document.getElementById("check-update-now");
  if (checkUpdateBtn) {
    checkUpdateBtn.addEventListener("click", async () => {
      checkUpdateBtn.disabled = true;
      const original = checkUpdateBtn.textContent;
      checkUpdateBtn.textContent = "Checking…";
      try {
        const res = await postForm("/admin/check-update", new FormData());
        if (res && res.update_available) {
          showToast("Update available: " + res.latest_version, "ok");
        } else if (res) {
          showToast("Up to date (" + res.current_version + ").", "ok");
        }
        refreshUpdateStatus();
      } catch (err) {
        showToast("Update check failed: " + err.message, "err");
      } finally {
        checkUpdateBtn.disabled = false;
        checkUpdateBtn.textContent = original;
      }
    });
  }
})();
