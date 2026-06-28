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

  // ---- EBC -> hex (SRM reference colour chart, mirrors app/colors.py) ----
  // SRM integer -> [r,g,b]. EBC is converted with SRM = EBC / 1.97.
  const SRM_RGB = {
    1:[0xFF,0xE6,0x99],2:[0xFF,0xD8,0x78],3:[0xFF,0xCA,0x5A],4:[0xFF,0xBF,0x42],
    5:[0xFB,0xB1,0x23],6:[0xF8,0xA6,0x00],7:[0xF3,0x9C,0x00],8:[0xEA,0x8F,0x00],
    9:[0xE5,0x85,0x00],10:[0xDE,0x7C,0x00],11:[0xD7,0x72,0x00],12:[0xCF,0x69,0x00],
    13:[0xCB,0x62,0x00],14:[0xC3,0x59,0x00],15:[0xBB,0x51,0x00],16:[0xB5,0x4C,0x00],
    17:[0xB0,0x45,0x00],18:[0xA6,0x3E,0x00],19:[0xA1,0x37,0x00],20:[0x9B,0x32,0x00],
    21:[0x95,0x2D,0x00],22:[0x8E,0x29,0x00],23:[0x88,0x23,0x00],24:[0x82,0x1E,0x00],
    25:[0x7B,0x1A,0x00],26:[0x77,0x19,0x00],27:[0x70,0x14,0x00],28:[0x6A,0x0E,0x00],
    29:[0x66,0x0D,0x00],30:[0x5E,0x0B,0x00],31:[0x5A,0x0A,0x02],32:[0x60,0x09,0x03],
    33:[0x52,0x09,0x07],34:[0x4C,0x05,0x05],35:[0x47,0x06,0x06],36:[0x44,0x06,0x07],
    37:[0x3F,0x07,0x08],38:[0x3B,0x06,0x07],39:[0x3A,0x07,0x0B],40:[0x36,0x08,0x0A],
  };
  const EBC_PER_SRM = 1.97;

  function srmToRgb(srm) {
    if (srm <= 1) return SRM_RGB[1];
    if (srm >= 40) return SRM_RGB[40];
    const lo = Math.floor(srm), hi = lo + 1, frac = srm - lo;
    const a = SRM_RGB[lo], b = SRM_RGB[hi];
    return [
      Math.round(a[0] + (b[0] - a[0]) * frac),
      Math.round(a[1] + (b[1] - a[1]) * frac),
      Math.round(a[2] + (b[2] - a[2]) * frac),
    ];
  }

  // Public per the spec: EBC -> #rrggbb, clamped to the chart range.
  function ebcToHex(ebc) {
    if (ebc === null || ebc === undefined || isNaN(ebc)) return "#cccccc";
    const [r, g, b] = srmToRgb(Number(ebc) / EBC_PER_SRM);
    return "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");
  }

  function relLuminance(r, g, b) {
    const lin = (c) => {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }

  // Contrast rule: light text on dark (high-EBC) swatches, dark on pale ones.
  function textColorFor(hex) {
    const h = hex.replace("#", "");
    if (h.length !== 6) return "#111";
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return relLuminance(r, g, b) < 0.4 ? "#f5f5f5" : "#161616";
  }

  // expose for debugging / tests
  window.ebcToHex = ebcToHex;
  window.textColorFor = textColorFor;

  // ---- DOM refs ----
  const stage = document.getElementById("stage");
  const dotsEl = document.getElementById("dots");
  const tickerEl = document.getElementById("ticker");
  const tickerText = document.getElementById("ticker-text");
  const bootError = document.getElementById("boot-error");

  // ---- state ----
  const state = {
    layoutKey: null,        // signature of the page layout (tap numbers per page)
    currentPage: 0,         // persists across polls
    cardEls: new Map(),     // tap number -> card element
    dataByTap: new Map(),   // tap number -> last rendered tap data
    pages: [],              // array of arrays of tap numbers
    announcement: null,
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

  function visibleTaps(board) {
    // Server marks vacant+hide_vacant taps as hidden; drop them so the grid
    // reflows to fill the screen with the remaining cards.
    return (board.taps || []).filter((t) => !t.hidden);
  }

  function tapSignature(t) {
    // Everything that affects the rendered card.
    return [
      t.vacant ? 1 : 0, t.name, t.abv, t.ibu, t.ebc,
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

    if (changed("name")) setText(card, ".name", t.name);
    if (changed("description")) setText(card, ".desc", t.description || "");
    if (changed("abv")) setText(card, '[data-stat="abv"] .v', fmtNum(t.abv, "%"));
    if (changed("ibu")) setText(card, '[data-stat="ibu"] .v', fmtNum(t.ibu));
    if (changed("ebc")) setText(card, '[data-stat="ebc"] .v', fmtNum(t.ebc));
    if (changed("ebc")) updateSwatch(card, t);
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
    const hex = ebcToHex(t.ebc);
    const txt = textColorFor(hex);
    const swatchVal = t.ebc === null || t.ebc === undefined ? "" : Math.round(t.ebc);
    return `
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name">${esc(t.name || "Tap " + t.tap)}</h2>
        <div class="swatch" style="background:${hex};color:${txt}">${swatchVal}</div>
      </div>
      <p class="desc">${esc(t.description || "")}</p>
      <div class="card-foot">
        <img class="thumb" alt="" src="${esc(t.image_url || "/img/placeholder")}">
        <div class="stats">
          <div class="stat" data-stat="abv"><span class="v">${fmtNum(t.abv, "%")}</span><span class="k">ABV</span></div>
          <div class="stat" data-stat="ibu"><span class="v">${fmtNum(t.ibu)}</span><span class="k">IBU</span></div>
          <div class="stat" data-stat="ebc"><span class="v">${fmtNum(t.ebc)}</span><span class="k">EBC</span></div>
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
    const hex = ebcToHex(t.ebc);
    sw.style.background = hex;
    sw.style.color = textColorFor(hex);
    sw.textContent = t.ebc === null || t.ebc === undefined ? "" : String(Math.round(t.ebc));
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
    const taps = visibleTaps(board);
    const pages = chunk(taps.map((t) => t.tap), MAX_CARDS_PER_PAGE);
    const key = layoutSignature(pages);
    if (!state.hasRendered || key !== state.layoutKey) {
      fullRender(board, taps);
    } else {
      diffUpdate(taps);
    }
    updateTicker(board.announcement_text);
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
