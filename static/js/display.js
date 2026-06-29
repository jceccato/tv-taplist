/* TV display logic: ebcToHex colour mapping, paginated carousel, and a
   diff-based poller that updates only changed cards in place.

   Design notes:
   - Carousel timer and data-poll timer are SEPARATE setInterval()s (both 30s)
     so a poll never resets the carousel position.
   - On poll we compute the page layout. If the layout (which taps sit on which
     page) is unchanged, we update only the cards whose data changed — no full
     grid re-render. If the layout changed (tap added/removed/hidden), we
     rebuild the pages but keep the current page index. */

(() => {
  "use strict";

  const POLL_MS = 30000;
  const FAST_RETRY_MS = 2000;   // quick retry until the first successful render
  const CAROUSEL_MS = 30000;
  const MAX_CARDS_PER_PAGE = 8;

  // Colour is computed server-side (app/colors.py: the ebc2hex polynomial plus a
  // per-tap saturation) and delivered with every tap as color_hex / text_color,
  // so the swatch, the glass placeholder and the API all agree and there is a
  // single implementation. Only the colour *stat* number (EBC<->SRM) is derived
  // here, from this conversion factor.
  const EBC_PER_SRM = 1.97;

  // ---- DOM refs ----
  const stage = document.getElementById("stage");
  const dotsEl = document.getElementById("dots");
  const tickerEl = document.getElementById("ticker");
  const tickerText = document.getElementById("ticker-text");
  const bootError = document.getElementById("boot-error");
  const venueHeader = document.getElementById("venue-header");
  const venueLogo = document.getElementById("venue-logo");

  // Default display settings until the first board arrives.
  const DEFAULT_SETTINGS = {
    color_unit: "ebc",
    show_abv: true, show_ibu: true, show_color: true,
    hide_abv_when_empty: true, hide_ibu_when_empty: true, hide_color_when_empty: true,
  };

  // ---- state ----
  const state = {
    layoutKey: null,        // signature of the page layout (tap numbers per page)
    currentPage: 0,         // persists across polls
    cardEls: new Map(),     // tap number -> card element
    dataByTap: new Map(),   // tap number -> last rendered tap data
    pages: [],              // array of arrays of tap numbers
    announcement: null,
    settings: { ...DEFAULT_SETTINGS },
    venueLogoSrc: null,
    hasRendered: false,
  };

  // ---- helpers ----
  function chunk(arr, size) {
    const out = [];
    for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
    return out;
  }

  function fmtNum(v, suffix = "") {
    if (v === null || v === undefined || v === "") return "—";
    return `${v}${suffix}`;
  }

  function isEmpty(v) {
    return v === null || v === undefined || v === "";
  }

  // A stat is hidden if globally disabled, or empty-and-configured-to-hide.
  function statHidden(value, show, hideWhenEmpty) {
    if (!show) return true;
    return isEmpty(value) && hideWhenEmpty;
  }

  // Colour stat label + value for the configured unit (values stored as EBC).
  function colorLabel() {
    return state.settings.color_unit === "srm" ? "SRM" : "EBC";
  }
  function colorValue(ebc) {
    if (isEmpty(ebc)) return "—";
    const v = state.settings.color_unit === "srm" ? Number(ebc) / EBC_PER_SRM : Number(ebc);
    return String(Math.round(v));
  }

  // Signature of the global display settings; a change forces a full re-render
  // so every card picks up the new unit / visibility rules immediately.
  function settingsSignature(s) {
    return [
      s.color_unit, s.show_abv, s.show_ibu, s.show_color,
      s.hide_abv_when_empty, s.hide_ibu_when_empty, s.hide_color_when_empty,
    ].join("|");
  }

  function visibleTaps(board) {
    // Server marks vacant+hide_vacant taps as hidden; drop them so the grid
    // reflows to fill the screen with the remaining cards.
    return (board.taps || []).filter((t) => !t.hidden);
  }

  function tapSignature(t) {
    // Everything that affects the rendered card.
    return [
      t.vacant ? 1 : 0, t.name, t.abv, t.ibu, t.ebc, t.color_hex,
      t.description, t.image_url, t.source,
    ].join("|");
  }

  function layoutSignature(pages) {
    return pages.map((p) => p.join(",")).join(";");
  }

  // ---- card building ----
  function buildCard(t) {
    const card = document.createElement("article");
    card.className = "card" + (t.vacant ? " vacant" : "");
    card.dataset.tap = String(t.tap);
    fillCard(card, t, true);
    return card;
  }

  function fillCard(card, t, force) {
    const prev = state.dataByTap.get(t.tap);
    const changed = (field) => force || !prev || prev[field] !== t[field];

    // Vacant <-> filled transition requires a structural refill.
    const wasVacant = prev ? prev.vacant : null;
    if (force || wasVacant !== t.vacant) {
      card.classList.toggle("vacant", !!t.vacant);
      card.innerHTML = t.vacant ? vacantInner(t) : filledInner(t);
      bindImage(card, t);
      return;
    }
    if (t.vacant) return; // nothing else to update on a vacant card

    const s = state.settings;
    if (changed("name")) setText(card, ".name", t.name);
    if (changed("description")) setText(card, ".desc", t.description || "");
    if (changed("abv")) {
      setText(card, '[data-stat="abv"] .v', fmtNum(t.abv, "%"));
      setHidden(card, '[data-stat="abv"]', statHidden(t.abv, s.show_abv, s.hide_abv_when_empty));
    }
    if (changed("ibu")) {
      setText(card, '[data-stat="ibu"] .v', fmtNum(t.ibu));
      setHidden(card, '[data-stat="ibu"]', statHidden(t.ibu, s.show_ibu, s.hide_ibu_when_empty));
    }
    if (changed("ebc")) {
      setText(card, '[data-stat="color"] .v', colorValue(t.ebc));
      setHidden(card, '[data-stat="color"]', statHidden(t.ebc, s.show_color, s.hide_color_when_empty));
    }
    // color_hex changes independently of ebc when saturation is overridden.
    if (changed("ebc") || changed("color_hex")) updateSwatch(card, t);
    if (changed("source")) setText(card, ".source-badge", sourceLabel(t.source));
    if (changed("image_url")) {
      const img = card.querySelector(".thumb");
      const next = t.image_url || "/img/placeholder";
      if (img && img.getAttribute("src") !== next) {
        delete img.dataset.fellBack; // allow the new image to fall back again
        img.src = next;
      }
    }
  }

  function sourceLabel(src) {
    if (src === "custom") return "Custom";
    if (src === "brewfather") return "BF";
    return "";
  }

  function filledInner(t) {
    const s = state.settings;
    const hex = t.color_hex || "#cccccc";
    const txt = t.text_color || "#f5f5f5";
    // Swatch is the colour circle only (no number — it's listed in the stats).
    const swatchHidden = statHidden(t.ebc, s.show_color, s.hide_color_when_empty);
    const abvHidden = statHidden(t.abv, s.show_abv, s.hide_abv_when_empty);
    const ibuHidden = statHidden(t.ibu, s.show_ibu, s.hide_ibu_when_empty);
    const colorHidden = swatchHidden;
    const hAttr = (h) => (h ? " hidden" : "");
    return `
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name">${esc(t.name || "Tap " + t.tap)}</h2>
        <div class="swatch" style="background:${hex};color:${txt}"${hAttr(swatchHidden)}></div>
      </div>
      <p class="desc">${esc(t.description || "")}</p>
      <div class="card-foot">
        <img class="thumb" alt="" src="${esc(t.image_url || "/img/placeholder")}">
        <div class="stats">
          <div class="stat" data-stat="abv"${hAttr(abvHidden)}><span class="v">${fmtNum(t.abv, "%")}</span><span class="k">ABV</span></div>
          <div class="stat" data-stat="ibu"${hAttr(ibuHidden)}><span class="v">${fmtNum(t.ibu)}</span><span class="k">IBU</span></div>
          <div class="stat" data-stat="color"${hAttr(colorHidden)}><span class="v">${colorValue(t.ebc)}</span><span class="k">${colorLabel()}</span></div>
        </div>
      </div>
      <span class="source-badge">${sourceLabel(t.source)}</span>`;
  }

  function vacantInner(t) {
    return `
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name">Vacant</h2>
      </div>
      <p class="desc">This tap is currently empty.</p>`;
  }

  function updateSwatch(card, t) {
    const sw = card.querySelector(".swatch");
    if (!sw) return;
    const s = state.settings;
    sw.style.background = t.color_hex || "#cccccc";
    sw.style.color = t.text_color || "#f5f5f5";
    sw.hidden = statHidden(t.ebc, s.show_color, s.hide_color_when_empty);
  }

  function bindImage(card, t) {
    const img = card.querySelector(".thumb");
    if (!img) return;
    // Never show a broken-image icon if a file vanished mid-cycle. Guard against
    // an infinite loop if the placeholder itself were ever unreachable.
    img.addEventListener("error", () => {
      if (img.dataset.fellBack === "1") return;
      img.dataset.fellBack = "1";
      img.src = "/img/placeholder";
    });
  }

  function setText(card, sel, value) {
    const el = card.querySelector(sel);
    if (el) el.textContent = value;
  }

  function setHidden(card, sel, hidden) {
    const el = card.querySelector(sel);
    if (el) el.hidden = !!hidden;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- rendering ----
  function fullRender(board, taps) {
    state.pages = chunk(taps.map((t) => t.tap), MAX_CARDS_PER_PAGE);
    state.layoutKey = layoutSignature(state.pages);
    state.cardEls.clear();
    stage.innerHTML = "";

    if (taps.length === 0) {
      const empty = document.createElement("div");
      empty.className = "page active";
      empty.dataset.count = "1";
      empty.innerHTML = `<article class="card vacant"><h2 class="name">No taps configured</h2><p class="desc">Set the number of taps in the admin panel.</p></article>`;
      stage.appendChild(empty);
      state.pages = [[]];
    } else {
      const byTap = new Map(taps.map((t) => [t.tap, t]));
      state.pages.forEach((pageTaps, idx) => {
        const page = document.createElement("div");
        page.className = "page";
        page.dataset.count = String(pageTaps.length);
        pageTaps.forEach((tapNo) => {
          const t = byTap.get(tapNo);
          const card = buildCard(t);
          state.cardEls.set(tapNo, card);
          page.appendChild(card);
        });
        stage.appendChild(page);
      });
    }

    // Remember new data and clamp the persisted page index.
    taps.forEach((t) => state.dataByTap.set(t.tap, t));
    if (state.currentPage >= state.pages.length) state.currentPage = 0;
    showPage(state.currentPage);
    renderDots();
    state.hasRendered = true;
  }

  function diffUpdate(taps) {
    // Layout unchanged: update only the cards whose data changed, in place.
    taps.forEach((t) => {
      const card = state.cardEls.get(t.tap);
      if (!card) return;
      const prev = state.dataByTap.get(t.tap);
      if (prev && tapSignature(prev) === tapSignature(t)) return; // unchanged
      fillCard(card, t, false);
      state.dataByTap.set(t.tap, t);
    });
  }

  function showPage(idx) {
    const pages = stage.querySelectorAll(".page");
    pages.forEach((p, i) => p.classList.toggle("active", i === idx));
    state.currentPage = idx;
    renderDots();
  }

  function renderDots() {
    const n = state.pages.length;
    if (n <= 1) { dotsEl.hidden = true; dotsEl.innerHTML = ""; return; }
    dotsEl.hidden = false;
    dotsEl.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const d = document.createElement("span");
      d.className = "dot" + (i === state.currentPage ? " on" : "");
      dotsEl.appendChild(d);
    }
  }

  function updateTicker(text) {
    const t = (text || "").trim();
    if (state.announcement === t) return;
    state.announcement = t;
    if (!t) { tickerEl.hidden = true; return; }
    tickerEl.hidden = false;
    tickerText.className = "ticker-text"; // reset so we measure intrinsic width
    tickerText.style.animationDuration = "";
    tickerText.textContent = t;
    // Measure synchronously (reading scrollWidth forces layout) rather than in
    // requestAnimationFrame, which would never fire on a backgrounded tab.
    // Static-center short messages; scroll long ones at a steady ~120px/s.
    const overflow = tickerText.scrollWidth > tickerEl.clientWidth + 2;
    if (overflow) {
      tickerText.classList.add("scroll");
      const dur = Math.max(12, Math.round(tickerText.scrollWidth / 120));
      tickerText.style.animationDuration = dur + "s";
    } else {
      tickerText.classList.add("static");
    }
  }

  // ---- carousel (independent timer) ----
  function carouselTick() {
    if (state.pages.length <= 1) return;
    const next = (state.currentPage + 1) % state.pages.length;
    showPage(next);
  }

  // ---- polling ----
  async function poll() {
    try {
      const resp = await fetch("/api/board", { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const board = await resp.json();
      applyBoard(board);
      bootError.hidden = true;
    } catch (err) {
      // Keep showing whatever is already on screen (offline robustness).
      console.warn("board poll failed:", err);
      if (!state.hasRendered) bootError.hidden = false;
    }
  }

  function applyBoard(board) {
    // Adopt the latest display settings before rendering cards.
    state.settings = {
      color_unit: board.color_unit || "ebc",
      show_abv: board.show_abv !== false,
      show_ibu: board.show_ibu !== false,
      show_color: board.show_color !== false,
      hide_abv_when_empty: board.hide_abv_when_empty !== false,
      hide_ibu_when_empty: board.hide_ibu_when_empty !== false,
      hide_color_when_empty: board.hide_color_when_empty !== false,
    };

    updateVenueHeader(board);

    const taps = visibleTaps(board);
    const pages = chunk(taps.map((t) => t.tap), MAX_CARDS_PER_PAGE);
    // Fold the settings signature into the layout key so a settings change
    // (e.g. EBC->SRM, or toggling a stat) forces a full re-render of all cards.
    const key = layoutSignature(pages) + "#" + settingsSignature(state.settings);
    if (!state.hasRendered || key !== state.layoutKey) {
      fullRender(board, taps);
      state.layoutKey = key;   // fullRender sets the layout-only key; override it
    } else {
      diffUpdate(taps);
    }
    updateTicker(board.announcement_text);
  }

  function updateVenueHeader(board) {
    const url = board.venue_logo_url;
    const h = Math.max(0, Math.min(33, Number(board.venue_logo_height_vh) || 0));
    if (!url || h <= 0) {
      venueHeader.hidden = true;
      document.documentElement.style.setProperty("--venue-h", "0px");
      return;
    }
    document.documentElement.style.setProperty("--venue-h", h + "vh");
    venueHeader.hidden = false;
    if (state.venueLogoSrc !== url) {
      state.venueLogoSrc = url;
      venueLogo.src = url;
    }
  }

  // ---- boot ----
  // Poll quickly until the first successful render, so a cold start or a backend
  // restart recovers within ~2s instead of waiting a full 30s cycle; then settle
  // into the steady 30s cadence. The carousel runs on its own independent timer.
  let pollTimer = null;
  async function pollLoop() {
    await poll();
    pollTimer = setTimeout(pollLoop, state.hasRendered ? POLL_MS : FAST_RETRY_MS);
  }
  pollLoop();
  setInterval(carouselTick, CAROUSEL_MS);
})();
