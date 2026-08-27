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

  // ---- card sizing (a preset picker + a scale slider, per axis) ----
  // Mirror TAP_PHOTO_PRESETS / TAP_TEXT_PRESETS in app/config_store.py. The
  // duplication is deliberate (no build step, so the browser cannot import the
  // Python maps) and is pinned by tests/test_frontend_constants.py so the two
  // cannot drift. These maps only drive what the operator SEES while picking:
  // the server re-resolves the preset on save, so a stale browser can never
  // store a preset beside another preset's scale.
  const TAP_PHOTO_PRESETS = {
    tiny: 0.4,
    small: 0.6,
    medium: 0.75,
    default: 1.0,
  };
  const TAP_TEXT_PRESETS = {
    small: 0.75,
    default: 1.0,
    large: 1.4,
  };

  function setupCardSizing(form) {
    // The readout lives in the <label>, outside the slider row, so look it up by
    // the control's id rather than by DOM proximity.
    function readout(input) {
      return form.querySelector('[data-range-val-for="' + input.id + '"]');
    }
    function showValue(input) {
      const el = readout(input);
      if (el) el.textContent = Number(input.value).toFixed(2);
    }

    // One axis: its picker, its slider, its preset map. Wiring them separately is
    // the point - the photo and the text are chosen independently, so nudging one
    // slider must never move the other axis's picker.
    function wireAxis(presetName, scaleName, presets) {
      const preset = form.querySelector('select[name="' + presetName + '"]');
      const scale = form.querySelector('input[name="' + scaleName + '"]');
      if (!preset || !scale) return;

      preset.addEventListener("change", () => {
        const fixed = presets[preset.value];
        // "Custom" has no number of its own: it means "keep what the slider
        // already shows", so picking it must not disturb it.
        if (fixed === undefined) return;
        scale.value = String(fixed);
        showValue(scale);
      });

      // Assigning .value above does not fire "input", so repainting the slider
      // from a preset cannot bounce the picker back to Custom. Only a human drag
      // reaches here.
      scale.addEventListener("input", () => {
        showValue(scale);
        preset.value = "custom";
      });

      showValue(scale);
    }

    wireAxis("tap_photo_preset", "tap_image_scale", TAP_PHOTO_PRESETS);
    wireAxis("tap_text_preset", "tap_text_scale", TAP_TEXT_PRESETS);
  }

  // ---- teaser label (issue #39): preset dropdown + custom text + counter ----
  // The dropdown's options are rendered by the server from
  // config_store.UPCOMING_LABEL_PRESETS, so there is nothing to mirror or drift
  // here - only the "which control currently owns the value" wiring, the same
  // shape as setupCardSizing's preset-vs-slider pattern above. The submitted
  // field is the hidden input; the visible select/text are just its UI.
  function setupUpcomingLabel(form) {
    const hidden = form.querySelector("#upcoming_label");
    const preset = document.getElementById("upcoming_label_preset");
    const custom = document.getElementById("upcoming_label_custom");
    const count = document.getElementById("upcoming_label_count");
    if (!hidden || !preset || !custom || !count) return;
    const max = Number(custom.maxLength) > 0 ? Number(custom.maxLength) : 32;

    function showCustom(show) {
      custom.hidden = !show;
      count.hidden = !show;
    }
    function syncCount() { count.textContent = custom.value.length + "/" + max; }
    syncCount();

    preset.addEventListener("change", () => {
      const isCustom = preset.value === "__custom";
      showCustom(isCustom);
      hidden.value = isCustom ? custom.value : preset.value;
      if (isCustom) custom.focus();
    });
    custom.addEventListener("input", () => {
      hidden.value = custom.value.slice(0, max);
      syncCount();
    });
  }

  // ---- settings form ----
  const settingsForm = document.getElementById("settings-form");
  if (settingsForm) {
    setupUpcomingLabel(settingsForm);

    // Keep the logo-height slider and number input in sync.
    const hRange = document.getElementById("logo_h_range");
    const hNum = document.getElementById("logo_h");
    if (hRange && hNum) {
      hRange.addEventListener("input", () => { hNum.value = hRange.value; });
      hNum.addEventListener("input", () => { hRange.value = hNum.value; });
    }

    setupCardSizing(settingsForm);

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
  // The server resolves which of four states applies; this file only chooses
  // wording. Do NOT re-derive the state here from update_available - that pair
  // cannot tell "current" from "could not compare", which is the whole of
  // issue #26. These strings mirror update_check.STATE_*; the drift guard in
  // tests/test_frontend_constants.py pins them.
  const UPDATE_STATE_DISABLED = "disabled";
  const UPDATE_STATE_UNKNOWN = "unknown";
  const UPDATE_STATE_BEHIND = "behind";
  const UPDATE_STATE_CURRENT = "current";

  const updateRow = document.getElementById("status-update-row");
  const updateLink = document.getElementById("status-update-link");
  const latestVersion = document.getElementById("status-latest-version");
  const updateNote = document.getElementById("status-update-note");
  const versionEl = document.getElementById("status-version");

  // One sentence for the state, used by both the toast and the status row, so
  // the button and the panel can never tell the operator two different things.
  function updateMessage(data) {
    const cur = data.current_version || "dev";
    const latest = data.latest_version;
    switch (data.status) {
      case UPDATE_STATE_BEHIND:
        return "Update available: " + latest;
      case UPDATE_STATE_CURRENT:
        return "Up to date (" + cur + ").";
      case UPDATE_STATE_DISABLED:
        return "Update checks are disabled.";
      default:
        // Untagged build, or nothing known to compare against. Say so plainly
        // rather than implying the container is current, which it may not be.
        return "Running an untagged build (" + cur + "). Update checks only apply"
          + " to tagged releases"
          + (latest ? "; latest release is " + latest + "." : ".");
    }
  }

  function renderUpdateStatus(data) {
    if (versionEl) versionEl.textContent = data.current_version || "dev";
    if (!updateRow) return;
    const state = data.status;
    // Nothing worth a row when the container is provably current, or when the
    // operator has turned checking off.
    if (state === UPDATE_STATE_CURRENT || state === UPDATE_STATE_DISABLED) {
      updateRow.hidden = true;
      return;
    }
    updateRow.hidden = false;
    const behind = state === UPDATE_STATE_BEHIND;
    if (updateLink) {
      updateLink.hidden = !data.latest_url;
      updateLink.textContent = behind ? "Update available" : "Latest release";
      updateLink.href = data.latest_url || "#";
    }
    if (latestVersion) latestVersion.textContent = data.latest_version || "";
    if (updateNote) {
      // The link already says "update available"; the note is only needed to
      // explain why an untagged build cannot be compared.
      updateNote.hidden = behind;
      updateNote.textContent = behind ? "" : updateMessage(data);
    }
  }

  async function refreshUpdateStatus() {
    try {
      const resp = await fetch("/api/update-status");
      if (!resp.ok) return;
      renderUpdateStatus(await resp.json());
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
        if (res) {
          // "unknown" is not a failure, but it is not an all-clear either, so
          // it gets the neutral toast rather than the ok one.
          const known = res.status === UPDATE_STATE_BEHIND
            || res.status === UPDATE_STATE_CURRENT;
          showToast(updateMessage(res), known ? "ok" : "warn");
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

  // ---- Snapshot: export + import ----
  // The import is two steps on purpose. Which Brewfather question to ask (if
  // any) depends on whether the *Snapshot* carries a key, which nothing here
  // can see - so the file is uploaded and validated first, and the server
  // answers with the case. This code renders the case it is handed; it never
  // works out which one applies. Uploading twice instead would mean sending a
  // Snapshot that can run to gigabytes over the venue's LAN a second time.
  const snapshotExportBtn = document.getElementById("snapshot-export");
  if (snapshotExportBtn) {
    snapshotExportBtn.addEventListener("click", () => {
      const opt = document.getElementById("snapshot-credentials");
      // A plain navigation, not fetch: the browser then owns the download,
      // including its progress UI on a Snapshot that takes minutes.
      const url = "/admin/snapshot" + (opt && opt.checked ? "?credentials=true" : "");
      window.location.assign(url);
    });
  }

  const snapshotForm = document.getElementById("snapshot-import-form");
  if (snapshotForm) {
    const fileInput = document.getElementById("snapshot-file");
    const panel = document.getElementById("snapshot-decision");
    const text = document.getElementById("snapshot-decision-text");
    const choice = document.getElementById("snapshot-decision-choice");
    const uploadBtn = document.getElementById("snapshot-import");
    const confirmBtn = document.getElementById("snapshot-confirm");
    const cancelBtn = document.getElementById("snapshot-cancel");

    function hidePanel() {
      panel.hidden = true;
      choice.hidden = true;
      text.textContent = "";
    }

    // One sentence per case, matching the server's `decision`. The strings live
    // here rather than being sent down because they are UI copy, but the choice
    // of which one to show is the server's.
    function describe(res) {
      if (res.decision === "environment") {
        return "This box's Brewfather key comes from an environment variable, so it will keep "
          + "syncing whatever happens here - an import cannot clear an environment variable. "
          + "The Snapshot's Brewfather beers are skipped, because the next sync would replace "
          + "them within minutes anyway. Everything else is imported.";
      }
      if (res.decision === "choose") {
        if (res.box_has_key && res.snapshot_has_key) {
          return "This box has a Brewfather key and so does the Snapshot. This box's own key is "
            + "kept - an import never replaces a working key. Choose what happens next:";
        }
        if (res.box_has_key) {
          return "This box has a Brewfather key in its settings. Choose what happens next:";
        }
        return "This Snapshot carries a Brewfather key. Restoring it would let this box sync, "
          + "and syncing rewrites every Brewfather tap. Choose what happens next:";
      }
      return "Neither this box nor the Snapshot has a Brewfather key, so nothing will sync over "
        + "the restored beers. Everything in the Snapshot is imported.";
    }

    snapshotForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        showToast("Choose a Snapshot zip first.", "err");
        return;
      }
      uploadBtn.disabled = true;
      const original = uploadBtn.textContent;
      uploadBtn.textContent = "Checking…";
      hidePanel();
      try {
        // The file IS the body - no multipart wrapper - so the server can
        // stream it straight to disk without a size cap meant for beer photos.
        const res = await postForm("/admin/snapshot/stage", file);
        text.textContent = describe(res);
        choice.hidden = res.decision !== "choose";
        panel.hidden = false;
      } catch (err) {
        showToast("Snapshot refused: " + err.message, "err");
      } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = original;
      }
    });

    confirmBtn.addEventListener("click", async () => {
      const picked = choice.hidden
        ? null
        : choice.querySelector('input[name="keep_syncing"]:checked');
      const fd = new FormData();
      // Blank means "the operator was never asked", which the server accepts
      // only for the two cases that carry no question.
      fd.append("keep_syncing", picked ? picked.value : "");
      confirmBtn.disabled = true;
      const original = confirmBtn.textContent;
      confirmBtn.textContent = "Restoring…";
      try {
        const res = await postForm("/admin/snapshot/import", fd);
        const skipped = res.counts.brewfather_skipped;
        showToast(
          "Snapshot restored: " + res.counts.taps + " tap file(s), "
          + res.counts.old_beers + " archived file(s)"
          + (skipped ? ", " + skipped + " Brewfather file(s) skipped" : "")
          + ". Reloading…", "ok");
        setTimeout(() => location.reload(), 1200);
      } catch (err) {
        showToast("Import failed: " + err.message, "err");
        confirmBtn.disabled = false;
        confirmBtn.textContent = original;
      }
    });

    cancelBtn.addEventListener("click", async () => {
      hidePanel();
      snapshotForm.reset();
      try {
        await postForm("/admin/snapshot/discard", new FormData());
      } catch (err) {
        // The staged copy is replaced by the next upload regardless, so a
        // failure here costs disk space and nothing else.
        showToast("Could not discard the uploaded Snapshot: " + err.message, "warn");
      }
    });
  }
})();
