/* TV display logic: themed, paginated carousel with a diff-based poller that
   updates only changed cards in place.

   Design notes:
   - Carousel timer and data-poll timer are SEPARATE timers so a poll never
     resets the carousel position. The carousel interval is operator-configurable
     (rotation_seconds) and is restarted on manual navigation.
   - On poll we compute the page layout. If the layout (which taps sit on which
     page) and the display settings are unchanged, we update only the cards whose
     data changed - no full grid re-render. Otherwise we rebuild, keeping the
     current page index.
   - Colour is computed server-side (app/colors.py) and delivered per tap as
     color_hex / text_color (honouring any per-beer override), so the swatch, the
     glass placeholder and the API all agree. Only the colour *stat* number
     (EBC<->SRM) is derived here, from this conversion factor. */

(() => {
  "use strict";

  const POLL_MS = 30000;
  const FAST_RETRY_MS = 2000;     // quick retry until the first successful render
  const DEFAULT_ROTATION_MS = 30000;
  const MAX_CARDS_PER_PAGE = 8;   // the per-count grid layouts are tuned up to 8
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
    show_abv: true, show_ibu: true, show_color: true, show_og: false, show_fg: false,
    hide_abv_when_empty: true, hide_ibu_when_empty: true, hide_color_when_empty: true,
    hide_og_when_empty: true, hide_fg_when_empty: true,
    show_source_badge: false,
    paginate: false, page_size: MAX_CARDS_PER_PAGE, rotation_seconds: 30,
  };

  // ---- state ----
  const state = {
    layoutKey: null,        // signature of the page layout + settings
    currentPage: 0,         // persists across polls
    cardEls: new Map(),     // tap number -> card element
    dataByTap: new Map(),   // tap number -> last rendered tap data
    pages: [],              // array of arrays of tap numbers
    announcement: null,
    settings: { ...DEFAULT_SETTINGS },
    themeKey: null,         // signature of the applied theme colours
    venueLogoSrc: null,
    hasRendered: false,
  };

  // ---- helpers ----
  function chunk(arr, size) {
    const out = [];
    const n = Math.max(1, size);
    for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
    return out;
  }

  // Taps per page: the operator's page_size when paginating, else fill to 8.
  function pageSize() {
    const s = state.settings;
    if (s.paginate) return Math.max(1, Math.min(MAX_CARDS_PER_PAGE, Number(s.page_size) || MAX_CARDS_PER_PAGE));
    return MAX_CARDS_PER_PAGE;
  }

  function fmtNum(v, suffix = "") {
    if (v === null || v === undefined || v === "") return "-";
    return `${v}${suffix}`;
  }

  function gravity(v) {
    if (isEmpty(v)) return "-";
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(3) : "-";
  }

  function isEmpty(v) {
    return v === null || v === undefined || v === "";
  }

  // A stat is hidden if globally disabled, or empty-and-configured-to-hide.
  function statHidden(value, show, hideWhenEmpty) {
    if (!show) return true;
    return isEmpty(value) && hideWhenEmpty;
  }

  // Effective per-tap visibility: a tri-state override (true/false) wins over the
  // global toggle; null/undefined inherits it.
  function effShow(perTap, globalShow) {
    return (perTap === true || perTap === false) ? perTap : globalShow;
  }

  function colorLabel() {
    return state.settings.color_unit === "srm" ? "SRM" : "EBC";
  }
  function colorValue(ebc) {
    if (isEmpty(ebc)) return "-";
    const v = state.settings.color_unit === "srm" ? Number(ebc) / EBC_PER_SRM : Number(ebc);
    return String(Math.round(v));
  }

  // Signature of the global display settings; a change forces a full re-render so
  // every card picks up the new unit / visibility rules immediately. (Rotation
  // and theme are applied separately and are deliberately excluded.)
  function settingsSignature(s) {
    return [
      s.color_unit, s.show_abv, s.show_ibu, s.show_color, s.show_og, s.show_fg,
      s.hide_abv_when_empty, s.hide_ibu_when_empty, s.hide_color_when_empty,
      s.hide_og_when_empty, s.hide_fg_when_empty, s.show_source_badge,
      s.paginate, s.page_size,
    ].join("|");
  }

  function visibleTaps(board) {
    return (board.taps || []).filter((t) => !t.hidden);
  }

  function tapSignature(t) {
    return [
      t.vacant ? 1 : 0, t.name, t.abv, t.ibu, t.ebc, t.og, t.fg, t.color_hex, t.color_known,
      t.show_og, t.show_fg, t.description, t.image_url, t.source,
    ].join("|");
  }

  function layoutSignature(pages) {
    return pages.map((p) => p.join(",")).join(";");
  }

  // ---- theme ----
  const THEME_VARS = {
    bg: "--bg", bg_card: "--bg-card", bg_card_2: "--bg-card-2", border: "--border",
    text: "--text", text_dim: "--text-dim", accent: "--accent", vacant: "--vacant",
  };
  function applyTheme(theme) {
    if (!theme || typeof theme !== "object") return;
    const key = JSON.stringify(theme);
    if (key === state.themeKey) return;
    state.themeKey = key;
    const root = document.documentElement;
    for (const k in THEME_VARS) {
      if (theme[k]) root.style.setProperty(THEME_VARS[k], theme[k]);
    }
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
      measureMarquee(card.querySelector(".name"));
      measureMarquee(card.querySelector(".desc"));
      return;
    }
    if (t.vacant) return; // nothing else to update on a vacant card

    const s = state.settings;
    if (changed("name")) { setText(card, ".name .scroller", t.name); measureMarquee(card.querySelector(".name")); }
    if (changed("description")) { setText(card, ".desc .scroller", t.description || ""); measureMarquee(card.querySelector(".desc")); }
    if (changed("abv")) {
      setText(card, '[data-stat="abv"] .v', fmtNum(t.abv, "%"));
      setHidden(card, '[data-stat="abv"]', statHidden(t.abv, s.show_abv, s.hide_abv_when_empty));
    }
    if (changed("ibu")) {
      setText(card, '[data-stat="ibu"] .v', fmtNum(t.ibu));
      setHidden(card, '[data-stat="ibu"]', statHidden(t.ibu, s.show_ibu, s.hide_ibu_when_empty));
    }
    if (changed("og") || changed("show_og")) {
      setText(card, '[data-stat="og"] .v', gravity(t.og));
      setHidden(card, '[data-stat="og"]', statHidden(t.og, effShow(t.show_og, s.show_og), s.hide_og_when_empty));
    }
    if (changed("fg") || changed("show_fg")) {
      setText(card, '[data-stat="fg"] .v', gravity(t.fg));
      setHidden(card, '[data-stat="fg"]', statHidden(t.fg, effShow(t.show_fg, s.show_fg), s.hide_fg_when_empty));
    }
    if (changed("ebc")) {
      setText(card, '[data-stat="color"] .v', colorValue(t.ebc));
      setHidden(card, '[data-stat="color"]', statHidden(t.ebc, s.show_color, s.hide_color_when_empty));
    }
    if (changed("ebc") || changed("color_hex") || changed("color_known")) updateSwatch(card, t);
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
    // The swatch tracks whether a colour is *known* (EBC or override); the colour
    // STAT number tracks EBC specifically, so an override-only beer shows a swatch
    // but no EBC number.
    const swatchHidden = !s.show_color || (!t.color_known && s.hide_color_when_empty);
    const abvHidden = statHidden(t.abv, s.show_abv, s.hide_abv_when_empty);
    const ibuHidden = statHidden(t.ibu, s.show_ibu, s.hide_ibu_when_empty);
    const ogHidden = statHidden(t.og, effShow(t.show_og, s.show_og), s.hide_og_when_empty);
    const fgHidden = statHidden(t.fg, effShow(t.show_fg, s.show_fg), s.hide_fg_when_empty);
    const colorHidden = statHidden(t.ebc, s.show_color, s.hide_color_when_empty);
    const hAttr = (h) => (h ? " hidden" : "");
    const badge = s.show_source_badge
      ? `<span class="source-badge">${sourceLabel(t.source)}</span>` : "";
    return `
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name"><span class="scroller">${esc(t.name || "Tap " + t.tap)}</span></h2>
        <div class="swatch" style="background:${hex};color:${txt}"${hAttr(swatchHidden)}></div>
      </div>
      <p class="desc"><span class="scroller">${esc(t.description || "")}</span></p>
      <div class="card-foot">
        <img class="thumb" alt="" src="${esc(t.image_url || "/img/placeholder")}">
        <div class="stats">
          <div class="stat" data-stat="abv"${hAttr(abvHidden)}><span class="v">${fmtNum(t.abv, "%")}</span><span class="k">ABV</span></div>
          <div class="stat" data-stat="ibu"${hAttr(ibuHidden)}><span class="v">${fmtNum(t.ibu)}</span><span class="k">IBU</span></div>
          <div class="stat" data-stat="og"${hAttr(ogHidden)}><span class="v">${gravity(t.og)}</span><span class="k">OG</span></div>
          <div class="stat" data-stat="fg"${hAttr(fgHidden)}><span class="v">${gravity(t.fg)}</span><span class="k">FG</span></div>
          <div class="stat" data-stat="color"${hAttr(colorHidden)}><span class="v">${colorValue(t.ebc)}</span><span class="k">${colorLabel()}</span></div>
        </div>
      </div>
      ${badge}`;
  }

  function vacantInner(t) {
    return `
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name"><span class="scroller">Vacant</span></h2>
      </div>
      <p class="desc"><span class="scroller">This tap is currently empty.</span></p>`;
  }

  function updateSwatch(card, t) {
    const sw = card.querySelector(".swatch");
    if (!sw) return;
    const s = state.settings;
    sw.style.background = t.color_hex || "#cccccc";
    sw.style.color = t.text_color || "#f5f5f5";
    sw.hidden = !s.show_color || (!t.color_known && s.hide_color_when_empty);
  }

  function bindImage(card, t) {
    const img = card.querySelector(".thumb");
    if (!img) return;
    img.addEventListener("error", () => {
      if (img.dataset.fellBack === "1") return;
      img.dataset.fellBack = "1";
      img.src = "/img/placeholder";
    });
  }

  // ---- auto-scrolling text (marquee) ----
  // Measure a clipping box (.name / .desc); if its content overflows, set the
  // shift + duration custom properties and switch on the vertical scroll.
  function measureMarquee(box) {
    if (!box) return;
    const scroller = box.querySelector(".scroller");
    if (!scroller) return;
    box.classList.remove("scrolling");
    box.style.removeProperty("--scroll-shift");
    box.style.removeProperty("--scroll-dur");
    const overflow = scroller.scrollHeight - box.clientHeight;
    if (overflow > 2) {
      const dur = Math.max(8, Math.round(overflow / 24) + 6); // steady, with paused ends
      box.style.setProperty("--scroll-shift", `-${overflow}px`);
      box.style.setProperty("--scroll-dur", `${dur}s`);
      box.classList.add("scrolling");
    }
  }

  function measureAllMarquees() {
    state.cardEls.forEach((card) => {
      measureMarquee(card.querySelector(".name"));
      measureMarquee(card.querySelector(".desc"));
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
    state.pages = chunk(taps.map((t) => t.tap), pageSize());
    state.layoutKey = layoutSignature(state.pages);
    state.cardEls.clear();
    stage.innerHTML = "";

    if (taps.length === 0) {
      const empty = document.createElement("div");
      empty.className = "page active";
      empty.dataset.count = "1";
      empty.innerHTML = `<article class="card vacant"><h2 class="name"><span class="scroller">No taps configured</span></h2><p class="desc"><span class="scroller">Set the number of taps in the admin panel.</span></p></article>`;
      stage.appendChild(empty);
      state.pages = [[]];
    } else {
      const byTap = new Map(taps.map((t) => [t.tap, t]));
      state.pages.forEach((pageTaps) => {
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

    taps.forEach((t) => state.dataByTap.set(t.tap, t));
    if (state.currentPage >= state.pages.length) state.currentPage = 0;
    showPage(state.currentPage);
    renderDots();
    measureAllMarquees();
    state.hasRendered = true;
  }

  function diffUpdate(taps) {
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

  // Jump to a page on manual navigation (dot click / keypress) and restart the
  // rotation timer so the page the operator chose isn't flipped away immediately.
  function goToPage(idx) {
    if (idx < 0 || idx >= state.pages.length || idx === state.currentPage) {
      if (idx === state.currentPage) restartCarousel();
      return;
    }
    showPage(idx);
    restartCarousel();
  }

  function nextPage() {
    if (state.pages.length <= 1) return;
    goToPage((state.currentPage + 1) % state.pages.length);
  }

  function renderDots() {
    const n = state.pages.length;
    if (n <= 1) { dotsEl.hidden = true; dotsEl.innerHTML = ""; return; }
    dotsEl.hidden = false;
    dotsEl.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const d = document.createElement("button");
      d.type = "button";
      d.className = "dot" + (i === state.currentPage ? " on" : "");
      d.setAttribute("aria-label", `Show page ${i + 1} of ${n}`);
      d.addEventListener("click", ((idx) => () => goToPage(idx))(i));
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
    const overflow = tickerText.scrollWidth > tickerEl.clientWidth + 2;
    if (overflow) {
      tickerText.classList.add("scroll");
      const dur = Math.max(12, Math.round(tickerText.scrollWidth / 120));
      tickerText.style.animationDuration = dur + "s";
    } else {
      tickerText.classList.add("static");
    }
  }

  // ---- carousel (independent, configurable timer) ----
  let carouselTimer = null;
  let carouselMs = DEFAULT_ROTATION_MS;
  function carouselTick() {
    if (state.pages.length <= 1) return;
    showPage((state.currentPage + 1) % state.pages.length);
  }
  function restartCarousel() {
    if (carouselTimer) clearInterval(carouselTimer);
    carouselTimer = setInterval(carouselTick, carouselMs);
  }
  function setRotation(seconds) {
    const ms = Math.max(3, Number(seconds) || 30) * 1000;
    if (ms === carouselMs && carouselTimer) return;
    carouselMs = ms;
    restartCarousel();
  }

  // ---- keyboard navigation: Enter / Space advance a page ----
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " " || e.code === "Space" || e.key === "Spacebar") {
      e.preventDefault();
      nextPage();
    }
  });

  // ---- polling ----
  async function poll() {
    try {
      const resp = await fetch("/api/board", { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const board = await resp.json();
      applyBoard(board);
      bootError.hidden = true;
    } catch (err) {
      console.warn("board poll failed:", err);
      if (!state.hasRendered) bootError.hidden = false;
    }
  }

  function applyBoard(board) {
    applyTheme(board.theme);

    state.settings = {
      color_unit: board.color_unit || "ebc",
      show_abv: board.show_abv !== false,
      show_ibu: board.show_ibu !== false,
      show_color: board.show_color !== false,
      show_og: board.show_og === true,
      show_fg: board.show_fg === true,
      hide_abv_when_empty: board.hide_abv_when_empty !== false,
      hide_ibu_when_empty: board.hide_ibu_when_empty !== false,
      hide_color_when_empty: board.hide_color_when_empty !== false,
      hide_og_when_empty: board.hide_og_when_empty !== false,
      hide_fg_when_empty: board.hide_fg_when_empty !== false,
      show_source_badge: board.show_source_badge === true,
      paginate: board.paginate === true,
      page_size: Number(board.page_size) || MAX_CARDS_PER_PAGE,
      rotation_seconds: Number(board.rotation_seconds) || 30,
    };

    setRotation(state.settings.rotation_seconds);
    updateVenueHeader(board);

    const taps = visibleTaps(board);
    const pages = chunk(taps.map((t) => t.tap), pageSize());
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

  // Re-measure scrolling text after a viewport change (font sizes are vmin-based).
  let resizeRAF = null;
  window.addEventListener("resize", () => {
    if (resizeRAF) cancelAnimationFrame(resizeRAF);
    resizeRAF = requestAnimationFrame(measureAllMarquees);
  });

  // ---- boot ----
  let pollTimer = null;
  async function pollLoop() {
    await poll();
    pollTimer = setTimeout(pollLoop, state.hasRendered ? POLL_MS : FAST_RETRY_MS);
  }
  pollLoop();
  restartCarousel();
})();
