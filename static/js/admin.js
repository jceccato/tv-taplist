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

  // A slider's readout shows two decimals (the scales) unless it asks for
  // fewer with data-range-digits (the overlay strength, a whole number).
  function digits(out) {
    const n = Number(out.dataset.rangeDigits);
    return Number.isInteger(n) && n >= 0 ? n : 2;
  }

  function setupCardSizing(form) {
    // The readout lives in the <label>, outside the slider row, so look it up by
    // the control's id rather than by DOM proximity.
    function readout(input) {
      return form.querySelector('[data-range-val-for="' + input.id + '"]');
    }
    function showValue(input) {
      const el = readout(input);
      if (el) el.textContent = Number(input.value).toFixed(digits(el));
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

  // The overlay strength (issue #63): a slider with no preset, so it only needs
  // its readout kept up.
  function setupOverlayStrength(form) {
    const strength = form.querySelector('input[name="album_overlay_strength"]');
    const out = form.querySelector('[data-range-val-for="album_overlay_strength"]');
    if (!strength || !out) return;
    strength.addEventListener("input", () => {
      out.textContent = Number(strength.value).toFixed(digits(out));
    });
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
    if (!hidden || !preset || !custom || !count) return null;
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

    // Redraw the visible controls from the hidden value, for when a profile's
    // Follow / Customise switch sets the value from outside.
    return function syncFromValue() {
      const known = Array.from(preset.options).some(
        (o) => o.value !== "__custom" && o.value === hidden.value);
      preset.value = known ? hidden.value : "__custom";
      custom.value = known ? "" : hidden.value;
      showCustom(!known);
      syncCount();
    };
  }

  // ---- photo album picker: ticked album names -> one JSON text field ----
  // The ticked names travel as a single hidden field holding a JSON array,
  // because the submit handler below folds every checkbox of one name into a
  // single true/false and an album name may hold any character. The visible
  // ticks carry no `name` for the same reason. Returns a function that redraws
  // the ticks from the hidden value, for the profile Follow / Customise switch.
  function setupAlbumPicker(form) {
    const hidden = form.querySelector("#album_selection");
    const picker = document.getElementById("album-picker");
    const all = form.querySelector("#album_all");
    if (!hidden || !picker) return null;
    const picks = () => Array.from(picker.querySelectorAll(".album-pick"));
    const read = () => {
      try { const v = JSON.parse(hidden.value); return Array.isArray(v) ? v : []; }
      catch (e) { return []; }
    };
    const dim = () => { picker.classList.toggle("dim", !!(all && all.checked)); };
    picks().forEach((box) => box.addEventListener("change", () => {
      hidden.value = JSON.stringify(picks().filter((b) => b.checked).map((b) => b.dataset.album));
    }));
    if (all) all.addEventListener("change", dim);
    dim();
    return function syncFromValue() {
      const names = read();
      picks().forEach((b) => { b.checked = names.includes(b.dataset.album); });
      dim();
    };
  }

  // ---- settings form ----
  const settingsForm = document.getElementById("settings-form");
  if (settingsForm) {
    const syncUpcomingLabel = setupUpcomingLabel(settingsForm);
    const syncAlbumPicker = setupAlbumPicker(settingsForm);

    // Keep the logo-height slider and number input in sync.
    const hRange = document.getElementById("logo_h_range");
    const hNum = document.getElementById("logo_h");
    if (hRange && hNum) {
      hRange.addEventListener("input", () => { hNum.value = hRange.value; });
      hNum.addEventListener("input", () => { hRange.value = hNum.value; });
    }

    setupCardSizing(settingsForm);
    setupOverlayStrength(settingsForm);

    // ---- Display profile: Follow / Customise per unit ----
    // Only present while a profile other than Default is being edited. A unit
    // set to Follow shows the Default profile's value with its controls
    // disabled. Flipping to Follow keeps what was typed, so flipping back before
    // Save restores it; Customise otherwise starts from the value on show (the
    // Default profile's, for a unit that was followed).
    const defaultsEl = document.getElementById("profile-defaults");
    if (defaultsEl) {
      const defaults = JSON.parse(defaultsEl.textContent);
      const defaultFor = (name) => {
        if (name in defaults) return defaults[name];
        if (name.startsWith("theme_") && defaults.theme_custom) return defaults.theme_custom[name.slice(6)];
        return undefined;
      };
      const refreshDerived = () => {
        document.querySelectorAll("[data-range-val-for]").forEach((out) => {
          const r = document.getElementById(out.dataset.rangeValFor);
          if (r) out.textContent = Number(r.value).toFixed(digits(out));
        });
        document.querySelectorAll(".color-field").forEach((f) => {
          const sw = f.querySelector('input[type="color"]');
          const tx = f.querySelector("[data-hex-text]");
          if (sw && tx) tx.value = sw.value;
        });
        const logoNum = document.getElementById("logo_h");
        const logoRange = document.getElementById("logo_h_range");
        if (logoNum && logoRange) logoRange.value = logoNum.value;
        if (syncUpcomingLabel) syncUpcomingLabel();
        if (syncAlbumPicker) syncAlbumPicker();
        const theme = document.getElementById("theme");
        if (theme) theme.dispatchEvent(new Event("change"));
      };
      document.querySelectorAll(".unit[data-unit]").forEach((unit) => {
        const sw = unit.querySelector(".unit-customise");
        if (!sw) return;
        const controls = () => Array.from(unit.querySelectorAll("input, select, textarea"))
          .filter((el) => el !== sw);
        const isBox = (el) => el.type === "checkbox" || el.type === "radio";
        let typed = null;
        const render = () => {
          unit.classList.toggle("following", !sw.checked);
          controls().forEach((el) => { el.disabled = !sw.checked; });
        };
        sw.addEventListener("change", () => {
          if (!sw.checked) {
            typed = controls().map((el) => (isBox(el) ? el.checked : el.value));
            controls().forEach((el) => {
              const v = el.name ? defaultFor(el.name) : undefined;
              if (v === undefined) return;
              if (isBox(el)) el.checked = !!v;
              else el.value = (v !== null && typeof v === "object") ? JSON.stringify(v) : String(v);
            });
          } else if (typed) {
            const saved = typed;
            controls().forEach((el, i) => {
              if (isBox(el)) el.checked = saved[i]; else el.value = saved[i];
            });
            typed = null;
          }
          refreshDerived();
          render();
        });
        render();
      });
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

  // ---- Display profiles: picker, create, rename, delete, copy ----
  const picker = document.getElementById("profile-picker");
  if (picker) {
    const newForm = document.getElementById("profile-new-form");
    const newName = document.getElementById("profile-new-name");
    const current = document.body.dataset.profile || "";
    const goto = (name) => {
      location.href = "/admin" + (name ? "?profile=" + encodeURIComponent(name) : "");
    };
    picker.addEventListener("change", () => {
      if (picker.value === "__new") {
        newForm.hidden = false;
        newName.focus();
        return;
      }
      goto(picker.value);
    });
    document.getElementById("profile-new-cancel").addEventListener("click", () => {
      newForm.hidden = true;
      picker.value = current;
    });
    newForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData();
      fd.set("name", newName.value);
      try {
        const res = await postForm("/admin/profiles", fd);
        goto(res.name);
      } catch (err) {
        showToast("Could not create the profile: " + err.message, "err");
      }
    });
    document.querySelectorAll("[data-profile-rename]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const old = btn.dataset.profileRename;
        const next = window.prompt("New name for " + old + ":", old);
        if (next === null || next.trim() === "" || next.trim().toLowerCase() === old) return;
        if (!window.confirm("Renaming breaks any start URL that uses " + old + ". Those screens "
          + "will show the Default profile until their URL is changed to the new name. Rename anyway?")) return;
        const fd = new FormData();
        fd.set("new_name", next);
        try {
          const res = await postForm("/admin/profiles/" + encodeURIComponent(old) + "/rename", fd);
          goto(current === old ? res.name : current);
        } catch (err) {
          showToast("Rename failed: " + err.message, "err");
        }
      });
    });
    document.querySelectorAll("[data-profile-delete]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.profileDelete;
        if (!window.confirm("Delete the " + name + " profile? Screens using it will show the Default profile.")) return;
        try {
          await postForm("/admin/profiles/" + encodeURIComponent(name) + "/delete", new FormData());
          goto(current === name ? "" : current);
        } catch (err) {
          showToast("Delete failed: " + err.message, "err");
        }
      });
    });
    document.querySelectorAll("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const text = btn.dataset.copy;
        try {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
          } else {
            // Plain http on a LAN is not a secure context: fall back to a
            // temporary text box and the old copy command.
            const box = document.createElement("textarea");
            box.value = text;
            document.body.appendChild(box);
            box.select();
            const ok = document.execCommand("copy");
            box.remove();
            if (!ok) throw new Error("copy refused");
          }
          showToast("Copied " + text, "ok");
        } catch (err) {
          showToast("Could not copy; select the address and copy it by hand.", "warn");
        }
      });
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
  // ---- Photos tab: upload + status (issue #57) ----
  // Every file is its own request (raw body, so the server streams it to disk),
  // two or three at a time, each with its own bar and Retry: one bad or huge file
  // never sinks the rest. Uploads go into the album being viewed.
  const photosPanel = document.getElementById("tab-photos");
  if (photosPanel) {
    const PHOTO_MAX_BYTES = 250 * 1024 * 1024;   // mirrors photo_library.MAX_UPLOAD_BYTES; the server decides
    const PHOTO_PARALLEL = 3;
    const albumSel = document.getElementById("photos-album");
    const drop = document.getElementById("photos-drop");
    const fileInput = document.getElementById("photos-file");
    // The extensions the photo library reads, rendered from its PHOTO_EXTENSIONS.
    const photoExts = new Set((fileInput.dataset.extensions || "").split(" ").filter(Boolean));
    const isPhotoName = (name) => photoExts.has((name.match(/\.[^.]+$/) || [""])[0].toLowerCase());
    const list = document.getElementById("photos-uploads");
    const progress = document.getElementById("photos-progress");
    const grid = document.getElementById("photos-grid");
    const keptNote = document.getElementById("album-kept");
    const empty = document.getElementById("photos-empty");
    const queue = [];
    const picked = new Set();          // ids of the selected photos in the grid
    const removedPicked = new Set();   // ids of the selected photos in Removed
    let active = 0;
    let pollTimer = null;
    let pending = 0;

    function setProgressText(st) {
      if (st.pending > 0) {
        progress.textContent = "Processing " + (st.ready + st.failed) + " of " + st.total;
      } else {
        progress.textContent = st.total + " photo" + (st.total === 1 ? "" : "s") + ": "
          + st.ready + " ready" + (st.failed ? ", " + st.failed + " could not be read" : "") + ".";
      }
    }

    async function refreshPhotos() {
      try {
        const resp = await fetch("/admin/photos/status?album=" + encodeURIComponent(albumSel.value));
        if (resp.ok) {
          const st = await resp.json();
          const wanted = albumSel.value;
          albumSel.replaceChildren(...st.albums.map((a) => {
            const o = document.createElement("option");
            o.value = o.textContent = a;
            return o;
          }));
          albumSel.value = st.albums.includes(wanted) ? wanted : st.album;
          pending = st.pending;
          const kept = st.non_photo_files || [];
          keptNote.hidden = kept.length === 0;
          keptNote.textContent = kept.length === 0 ? "" : "This album holds files that are not photos, "
            + "so deleting it would keep the folder: " + kept.slice(0, 5).join(", ")
            + (kept.length > 5 ? " and " + (kept.length - 5) + " more" : "")
            + ". Remove them from the folder on disk to let it go.";
          setProgressText(st);
          const present = new Set(st.photos.map((p) => p.id));
          for (const id of Array.from(picked)) if (!present.has(id)) picked.delete(id);
          grid.replaceChildren(...st.photos.map((p) => {
            const li = document.createElement("li");
            li.className = "photo-tile " + p.state + (picked.has(p.id) ? " picked" : "");
            li.dataset.state = p.state;
            li.dataset.name = p.name;
            li.dataset.id = p.id;
            li.append(pickBox(p.name, picked.has(p.id), (on) => {
              if (on) picked.add(p.id); else picked.delete(p.id);
              li.classList.toggle("picked", on);
              updateSelection();
            }));
            if (p.thumb) {
              const img = document.createElement("img");
              img.src = p.thumb; img.alt = p.name; img.loading = "lazy";
              li.append(img);
            } else {
              const ph = document.createElement("div");
              ph.className = "photo-ph";
              li.append(ph);
            }
            const cap = document.createElement("span");
            cap.className = "photo-name";
            cap.textContent = p.name;
            const state = document.createElement("span");
            state.className = "photo-state";
            state.textContent = p.state === "ready" ? "Ready"
              : p.state === "waiting" ? "Waiting"
              : "Failed: " + (p.reason || "unknown reason");
            li.append(cap, state);
            if (p.low_res) {
              const soft = document.createElement("span");
              soft.className = "photo-lowres";
              soft.textContent = "Low resolution, will look soft";
              li.append(soft);
            }
            return li;
          }));
          moveTo.replaceChildren(...st.albums.filter((a) => a !== albumSel.value).map((a) => {
            const o = document.createElement("option");
            o.value = o.textContent = a;
            return o;
          }));
          updateSelection();
          empty.hidden = st.photos.length > 0;
        }
      } catch (_) { /* offline: keep the last view */ }
      refreshRemoved();
      schedulePoll();
    }

    // ---- organising (issue #58): albums, multi-select, Removed ----
    const selbar = document.getElementById("photos-selbar");
    const selCount = document.getElementById("photos-selcount");
    const moveTo = document.getElementById("photos-moveto");
    const moveBtn = document.getElementById("photos-move");
    const removedGrid = document.getElementById("removed-grid");
    const removedBar = document.getElementById("removed-bar");
    const removedSelCount = document.getElementById("removed-selcount");
    const removedEmpty = document.getElementById("removed-empty");
    const removedEmptyAll = document.getElementById("removed-empty-all");
    const removedCount = document.getElementById("removed-count");

    function pickBox(name, checked, onChange) {
      const box = document.createElement("input");
      box.type = "checkbox"; box.className = "photo-pick"; box.checked = checked;
      box.setAttribute("aria-label", "Select " + name);
      box.addEventListener("change", () => onChange(box.checked));
      return box;
    }

    function updateSelection() {
      selbar.hidden = picked.size === 0;
      selCount.textContent = picked.size + " selected";
      moveBtn.disabled = moveTo.options.length === 0;
      moveTo.hidden = moveTo.options.length === 0;
    }

    function updateRemovedSelection() {
      removedBar.hidden = removedPicked.size === 0;
      removedSelCount.textContent = removedPicked.size + " selected";
    }

    function idsForm(set, extra) {
      const fd = new FormData();
      for (const id of set) fd.append("ids", id);
      for (const [k, v] of Object.entries(extra || {})) fd.set(k, v);
      return fd;
    }

    async function act(fn, failure) {
      try { await fn(); } catch (err) { showToast(failure + ": " + err.message, "err"); }
      await refreshPhotos();
    }

    async function refreshRemoved() {
      try {
        const resp = await fetch("/admin/photos/removed");
        if (!resp.ok) return;
        const st = await resp.json();
        const present = new Set(st.photos.map((p) => p.id));
        for (const id of Array.from(removedPicked)) if (!present.has(id)) removedPicked.delete(id);
        removedGrid.replaceChildren(...st.photos.map((p) => {
          const li = document.createElement("li");
          li.className = "photo-tile removed" + (removedPicked.has(p.id) ? " picked" : "");
          li.dataset.id = p.id;
          li.dataset.name = p.name;
          li.append(pickBox(p.name, removedPicked.has(p.id), (on) => {
            if (on) removedPicked.add(p.id); else removedPicked.delete(p.id);
            li.classList.toggle("picked", on);
            updateRemovedSelection();
          }));
          if (p.thumb) {
            const img = document.createElement("img");
            img.src = p.thumb; img.alt = p.name; img.loading = "lazy";
            li.append(img);
          } else {
            const ph = document.createElement("div");
            ph.className = "photo-ph";
            li.append(ph);
          }
          const cap = document.createElement("span");
          cap.className = "photo-name";
          cap.textContent = p.name;
          const note = document.createElement("span");
          note.className = "photo-state";
          note.textContent = "From " + p.album + ", "
            + (p.days_left === 0 ? "purged tonight" : p.days_left + " day" + (p.days_left === 1 ? "" : "s") + " left");
          li.append(cap, note);
          return li;
        }));
        removedEmpty.hidden = st.photos.length > 0;
        removedEmptyAll.hidden = st.photos.length === 0;
        removedCount.textContent = st.photos.length ? "(" + st.photos.length + ")" : "";
        updateRemovedSelection();
      } catch (_) { /* offline: keep the last view */ }
    }

    document.getElementById("album-new").addEventListener("click", () => {
      const name = window.prompt("Name for the new album:");
      if (name === null || name.trim() === "") return;
      act(async () => {
        const fd = new FormData(); fd.set("name", name);
        const res = await postForm("/admin/photos/albums", fd);
        await refreshPhotos();
        albumSel.value = res.name;
      }, "Could not create the album");
    });
    document.getElementById("album-rename").addEventListener("click", () => {
      const old = albumSel.value;
      if (old === "Unsorted") { showToast("Unsorted is for loose photos and cannot be renamed.", "err"); return; }
      const name = window.prompt("New name for " + old + ":", old);
      if (name === null || name.trim() === "" || name.trim() === old) return;
      act(async () => {
        const fd = new FormData(); fd.set("old", old); fd.set("new", name);
        const res = await postForm("/admin/photos/albums/rename", fd);
        await refreshPhotos();
        albumSel.value = res.name;
      }, "Rename failed");
    });
    document.getElementById("album-delete").addEventListener("click", () => {
      const name = albumSel.value;
      if (name === "Unsorted") { showToast("Unsorted holds the loose photos and cannot be deleted. Select its photos and Remove them instead.", "err"); return; }
      if (!window.confirm("Delete the album " + name + "? Its photos move to Removed, where you can restore them.")) return;
      act(async () => {
        const fd = new FormData(); fd.set("name", name);
        const res = await postForm("/admin/photos/albums/delete", fd);
        picked.clear();
        if (res.kept_files && res.kept_files.length) {
          // The folder stays because of files that are not photos: stay on it, where
          // the note under the album bar names them.
          albumSel.value = name;
          showToast("The photos moved to Removed. The album stays because it holds files that are not photos.");
        } else {
          albumSel.value = "Unsorted";
        }
      }, "Delete failed");
    });
    moveBtn.addEventListener("click", () => act(async () => {
      const res = await postForm("/admin/photos/move", idsForm(picked, { album: moveTo.value }));
      picked.clear();
      showToast("Moved " + res.moved + " photo" + (res.moved === 1 ? "" : "s") + " to " + moveTo.value + ".");
    }, "Move failed"));
    document.getElementById("photos-remove").addEventListener("click", () => {
      if (!window.confirm("Remove " + picked.size + " photo" + (picked.size === 1 ? "" : "s")
        + "? They move to Removed, where you can restore them.")) return;
      act(async () => {
        await postForm("/admin/photos/remove", idsForm(picked));
        picked.clear();
      }, "Remove failed");
    });
    document.getElementById("photos-clear").addEventListener("click", () => { picked.clear(); refreshPhotos(); });
    document.getElementById("removed-restore").addEventListener("click", () => act(async () => {
      await postForm("/admin/photos/removed/restore", idsForm(removedPicked));
      removedPicked.clear();
    }, "Restore failed"));
    document.getElementById("removed-delete").addEventListener("click", () => {
      if (!window.confirm("Delete " + removedPicked.size + " photo" + (removedPicked.size === 1 ? "" : "s")
        + " forever? This cannot be undone.")) return;
      act(async () => {
        await postForm("/admin/photos/removed/delete", idsForm(removedPicked));
        removedPicked.clear();
      }, "Delete failed");
    });
    removedEmptyAll.addEventListener("click", () => {
      if (!window.confirm("Delete everything in Removed forever? This cannot be undone.")) return;
      act(async () => {
        await postForm("/admin/photos/removed/empty", new FormData());
        removedPicked.clear();
      }, "Empty failed");
    });
    document.getElementById("copies-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const res = await postForm("/admin/photos/pipeline", new FormData(e.target));
        document.getElementById("photo-format").value = res.format;
        document.getElementById("photo-size").value = res.size;
        showToast("Saved. Photos are being rebuilt as " + (res.format === "jpeg" ? "JPEG" : "WebP")
          + " at " + (res.size === "1080p" ? "1080p" : "4K") + "; screens keep the old copies until then.");
        refreshPhotos();
      } catch (err) { showToast("Could not save: " + err.message, "err"); }
    });
    document.getElementById("retention-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const res = await postForm("/admin/photos/retention", new FormData(e.target));
        document.getElementById("retention-days").value = res.days;
        showToast("Removed photos are purged after " + res.days + " day" + (res.days === 1 ? "" : "s") + ".");
        refreshRemoved();
      } catch (err) { showToast("Could not save: " + err.message, "err"); }
    });

    function schedulePoll() {
      clearTimeout(pollTimer);
      // Keep looking while photos are processing or files are still going up.
      if (!photosPanel.hidden && (pending > 0 || active > 0 || queue.length > 0)) {
        pollTimer = setTimeout(refreshPhotos, 2500);
      }
    }

    function addRow(item) {
      const li = document.createElement("li");
      li.className = "upload-row";
      const name = document.createElement("span");
      name.className = "upload-name";
      name.textContent = item.file.name;
      const bar = document.createElement("progress");
      bar.max = 100; bar.value = 0;
      const msg = document.createElement("span");
      msg.className = "upload-msg";
      const retry = document.createElement("button");
      retry.type = "button"; retry.className = "btn upload-retry"; retry.textContent = "Retry"; retry.hidden = true;
      retry.addEventListener("click", () => { retry.hidden = true; enqueue(item); });
      li.append(name, bar, msg, retry);
      list.append(li);
      item.ui = { li, bar, msg, retry };
    }

    function setRow(item, state, text, canRetry) {
      item.ui.li.dataset.state = state;
      item.ui.msg.textContent = text;
      item.ui.retry.hidden = !(state === "failed" && canRetry !== false);
    }

    function enqueue(item) {
      setRow(item, "waiting", "Waiting");
      item.ui.bar.value = 0;
      queue.push(item);
      pump();
    }

    function pump() {
      while (active < PHOTO_PARALLEL && queue.length) {
        const item = queue.shift();
        active++;
        sendPhoto(item).finally(() => { active--; pump(); if (!active && !queue.length) refreshPhotos(); });
      }
      schedulePoll();
    }

    function sendPhoto(item) {
      return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        const url = "/admin/photos/upload?album=" + encodeURIComponent(item.album)
          + "&name=" + encodeURIComponent(item.file.name);
        xhr.open("POST", url);
        setRow(item, "uploading", "Uploading");
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) item.ui.bar.value = Math.round(100 * e.loaded / e.total);
        };
        xhr.onload = () => {
          let body = null;
          try { body = JSON.parse(xhr.responseText); } catch (_) { /* non-JSON */ }
          if (xhr.status >= 200 && xhr.status < 300 && body) {
            item.ui.bar.value = 100;
            if (body.outcome === "duplicate") {
              setRow(item, "duplicate", "Skipped: already in the library as "
                + (body.album === "Unsorted" ? "" : body.album + "/") + body.name);
            } else {
              setRow(item, "done", body.renamed ? "Added as " + body.name + " (that name was taken)" : "Added");
            }
          } else if (xhr.status === 401) {
            setRow(item, "failed", "Signed out. Sign in again, then retry.");
          } else {
            setRow(item, "failed", (body && body.detail) || ("Failed (HTTP " + xhr.status + ")"));
          }
          resolve();
        };
        xhr.onerror = () => { setRow(item, "failed", "The connection failed."); resolve(); };
        xhr.onabort = xhr.onerror;
        xhr.send(item.file);
      });
    }

    function addFiles(files) {
      for (const file of files) {
        const item = { file, album: albumSel.value };
        addRow(item);
        if (!isPhotoName(file.name)) {
          setRow(item, "failed", "Not a photo format this can read (" + fileInput.dataset.formatNames + ").", false);
        } else if (file.size > PHOTO_MAX_BYTES) {
          setRow(item, "failed", "Over the 250 MB limit for one photo.", false);
        } else {
          enqueue(item);
        }
      }
    }

    fileInput.addEventListener("change", () => { addFiles(Array.from(fileInput.files)); fileInput.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault(); drop.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault(); drop.classList.remove("over");
    }));
    drop.addEventListener("drop", (e) => addFiles(Array.from(e.dataTransfer.files)));
    albumSel.addEventListener("change", () => { picked.clear(); refreshPhotos(); });
    document.querySelectorAll('.tab[data-tab="photos"]').forEach((t) => t.addEventListener("click", refreshPhotos));
    if (!photosPanel.hidden) refreshPhotos();
  }
})();
