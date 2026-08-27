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
   - Colour is resolved server-side (app/colors.py resolve_color) and delivered
     per tap as color_hex / text_color, so the swatch, the glass placeholder and
     the API all agree. Both are NULL when the beer's Colour is Unknown (no EBC
     and no override): the grey this file falls back to is the swatch's own
     declared fallback, not a copy of a server value - the placeholder glass
     declares a different one (amber) on purpose. See ADR-0004. Only the colour
     *stat* number (EBC<->SRM) is derived here, from this conversion factor.
   - Visibility is resolved server-side too (app/board.py resolve_visibility) and
     delivered per tap as six booleans - abv_visible, ibu_visible, ebc_visible,
     og_visible, fg_visible, swatch_visible. This file renders what it is told
     and must NOT re-derive them: the per-Tap override, the global toggle and
     Empty suppression are one documented chain (CONTEXT.md, Visibility) and it
     lives in exactly one place. The raw toggles are no longer on the wire. */

(() => {
  "use strict";

  const POLL_MS = 30000;
  const FAST_RETRY_MS = 2000;     // quick retry until the first successful render
  const DEFAULT_ROTATION_MS = 30000;
  const MAX_CARDS_PER_PAGE = 8;   // the per-count grid layouts are tuned up to 8
  const EBC_PER_SRM = 1.97;
  // Minimum horizontal travel, in CSS pixels, before a touch counts as a swipe.
  // Deliberately a constant here and not an operator setting: it describes how a
  // finger behaves on glass, not anything about the venue, so there is nothing
  // for an operator to decide. Keeping it client-only also keeps it out of the
  // config schema, the admin form and the board payload.
  const SWIPE_MIN_PX = 50;

  // ---- DOM refs ----
  const board = document.getElementById("board");
  const stage = document.getElementById("stage");
  const dotsEl = document.getElementById("dots");
  const tickerEl = document.getElementById("ticker");
  const tickerText = document.getElementById("ticker-text");
  const bootError = document.getElementById("boot-error");
  const venueHeader = document.getElementById("venue-header");
  const venueLogo = document.getElementById("venue-logo");

  // Default display settings until the first board arrives. Every key here is a
  // hand-copy of a server default (config_store.DEFAULT_CONFIG) - there is no
  // build step to share them - so tests/test_frontend_constants.py pins the
  // values and fails if either side drifts. The Visibility toggles used to live
  // here too; they left when the board started sending resolved answers, and the
  // same guard fails if one reappears.
  const DEFAULT_SETTINGS = {
    color_unit: "ebc",
    show_source_badge: false,
    paginate: false, page_size: 6, rotation_seconds: 30,
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
    photoScale: 1,          // last resolved photo scale, re-applied on every remeasure
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

  function colorLabel() {
    return state.settings.color_unit === "srm" ? "SRM" : "EBC";
  }
  function colorValue(ebc) {
    if (isEmpty(ebc)) return "-";
    const v = state.settings.color_unit === "srm" ? Number(ebc) / EBC_PER_SRM : Number(ebc);
    return String(Math.round(v));
  }

  // Signature of the global display settings; a change forces a full re-render so
  // every card picks up the new unit immediately. (Rotation and theme are applied
  // separately and are deliberately excluded.) The Visibility toggles are not
  // here any more and do not need to be: a toggle flip now changes the resolved
  // booleans on every tap, which tapSignature catches, and the diff path updates
  // the cards in place instead of rebuilding the grid.
  function settingsSignature(s) {
    return [
      s.color_unit, s.show_source_badge,
      s.paginate, s.page_size,
      // A scale change resizes every font, which invalidates the marquee
      // overflow measurements, so it has to force a full re-render rather than
      // a diff update. (Theme and rotation genuinely do not, hence their absence.)
      s.tap_image_scale, s.tap_text_scale,
    ].join("|");
  }

  function visibleTaps(board) {
    return (board.taps || []).filter((t) => !t.hidden);
  }

  // A Vacant Slot with a pinned Upcoming Beer (issue #38) renders a teaser card
  // in that Slot instead of the dashed Vacant card. `pinned` already answers
  // "does this Slot show a teaser permanently" - board.py decides that from the
  // Slot's own vacancy, so this file only reads the answer and never asks
  // whether a Slot is Vacant itself. When two Upcoming Beers are pinned to the
  // same Slot (CONTEXT.md: "there is no dedup"), `board.upcoming` is already in
  // display order, so the first one wins the Slot's one card - a display
  // bookkeeping choice, not a re-run of any domain rule.
  function pinnedTeasersBySlot(board) {
    const map = new Map();
    (board.upcoming || []).forEach((u) => {
      if (u.pinned && u.slot != null && !map.has(u.slot)) map.set(u.slot, u);
    });
    return map;
  }

  // Substitutes a pinned teaser's fields onto its Vacant Slot's tap entry. The
  // result is drawn with the exact same card renderer a Tap uses (`filledInner`)
  // - name, stats, swatch and image all come from the teaser's own resolved
  // answers - with only a `teaser` marker added so the card picks up the dashed
  // amber border instead of the Vacant stripes. No size or layout decision is
  // made here: a teaser occupies its Slot's normal position in the normal grid,
  // from the same Settings as every other card (CLAUDE.md: "no size option").
  function withTeasers(board) {
    const pinned = pinnedTeasersBySlot(board);
    // The ribbon's text (issue #39) is a board-level fact - one label for
    // every teaser - not a per-teaser answer, so it is read once here rather
    // than carried on each entry in `board.upcoming`. Absent whenever
    // `upcoming` itself is (the feature is off), which never reaches this
    // branch anyway since `pinned` would then be empty.
    const label = board.upcoming_label || "Coming up";
    return (board.taps || []).map((t) => {
      if (!t.vacant) return t;
      const u = pinned.get(t.tap);
      if (!u) return t;
      return {
        ...t,
        vacant: false,
        teaser: true,
        name: u.name, abv: u.abv, ibu: u.ibu, ebc: u.ebc, og: u.og, fg: u.fg,
        color_hex: u.color_hex, text_color: u.text_color,
        description: u.description, image_url: u.image_url,
        // An Upcoming Beer has no Source (CONTEXT.md: it is a projection of a
        // Batch, not a Tap) - null renders no badge label, same as today's
        // Vacant card.
        source: null,
        abv_visible: u.abv_visible, ibu_visible: u.ibu_visible,
        ebc_visible: u.ebc_visible, og_visible: u.og_visible,
        fg_visible: u.fg_visible, swatch_visible: u.swatch_visible,
        // The teaser's own words (issue #39): all three are resolved answers
        // off the wire (board.py's resolve_upcoming) - this file reads them
        // and never recomputes a status word, a subtitle, or whether an ABV
        // counts as an estimate.
        teaser_label: label, status_label: u.status_label, subtitle: u.subtitle,
        abv_estimated: u.abv_estimated,
      };
    });
  }

  // The six resolved Visibility booleans are part of the signature, not just the
  // values: a global toggle no longer reaches this file at all, so a flip is only
  // observable as a change in the answers the board sends. `teaser` joins them
  // for the same reason a pinned teaser can replace another pinned teaser (or a
  // real Tap can reclaim its Slot) without the `vacant` flag itself changing -
  // see the structural-refill check in fillCard().
  function tapSignature(t) {
    return [
      t.vacant ? 1 : 0, t.teaser ? 1 : 0, t.name, t.abv, t.ibu, t.ebc, t.og, t.fg,
      t.color_hex, t.abv_visible, t.ibu_visible, t.ebc_visible, t.og_visible,
      t.fg_visible, t.swatch_visible, t.description, t.image_url, t.source,
      // The teaser's own words (issue #39): a different pinned teaser can take
      // over a Vacant Slot without `vacant`/`teaser` themselves changing (the
      // structural-refill check in fillCard only catches THAT transition), so
      // these have to be part of the signature too or a swap would go unseen.
      t.teaser_label, t.status_label, t.subtitle, t.abv_estimated,
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

  // ---- card sizing ----
  // The board sends two already-resolved numbers (the presets that produced them
  // stay in Settings). The TEXT scale reaches the CSS the same way the theme
  // does: as a custom property on the document root, which display.css applies to
  // its preferred vmin size and its px ceiling, leaving the px floor alone so no
  // scale can push the board below legibility.
  const SCALE_VARS = {
    tap_text_scale: "--tap-text-scale",
  };
  function applyCardScales(board) {
    const root = document.documentElement;
    for (const k in SCALE_VARS) {
      // A missing or junk value falls back to 1 rather than writing "NaN" into
      // the property, which would make every scaled size invalid at once.
      const n = Number(board[k]);
      root.style.setProperty(SCALE_VARS[k], String(n > 0 ? n : 1));
    }
    applyPhotoScale(board.tap_image_scale);
  }

  // The PHOTO scale cannot be a CSS lever: a percentage cap resolves against the
  // card foot, but `object-fit: contain` usually makes the photo's width the
  // binding constraint, so most of the range sat above the size the photo already
  // rendered at and did nothing. So measure the height each photo actually
  // renders at with no cap, then cap that. At scale 1 the cap equals the measured
  // height, which is why the default look is byte-for-byte the old one.
  //
  // The price is that the cap is an ABSOLUTE px value and therefore goes stale
  // whenever the rendered size changes: it has to be re-applied on a re-render, a
  // viewport change, a stage layout change, and on each photo's own load. Every
  // caller below funnels through scheduleRemeasure().
  function applyPhotoScale(scale) {
    // A junk or out-of-range value renders at 1 rather than collapsing every
    // photo to 0px, which is what a stray NaN or a negative would do here.
    const n = Number(scale);
    state.photoScale = n > 0 ? Math.min(n, 1) : 1;
    const imgs = Array.prototype.slice.call(document.querySelectorAll(".card .thumb"));
    if (!imgs.length) return;
    // Clear every cap first, take ONE reflow, then measure them all: measuring
    // per image would force a synchronous reflow per card.
    imgs.forEach((img) => { img.style.maxHeight = "none"; });
    void document.body.offsetHeight;
    // Measure the height the photo is actually PAINTED at, not the height of
    // its box. `object-fit: contain` letterboxes a photo whose width is the
    // binding constraint - a 16:9 Brewfather shot in the wide-card layout,
    // where `max-width: 46%` decides the width - so the box can be taller than
    // anything the eye sees. Capping the box then does nothing until the cap
    // drops below the painted height, which is why scales above about 0.85
    // appeared to be dead on exactly those photos and worked fine on square
    // ones. naturalWidth is 0 until the photo decodes, so fall back to the box
    // and let the `load` handler re-run this with the real aspect ratio.
    const natural = imgs.map((img) => {
      const box = img.getBoundingClientRect();
      if (!img.naturalWidth || !img.naturalHeight) return box.height;
      return Math.min(box.height, box.width * img.naturalHeight / img.naturalWidth);
    });
    imgs.forEach((img, i) => {
      // A card on a page that is mid-transition can measure 0; leaving the cap
      // off is the safe answer, because the next remeasure will set it and a
      // 0px cap would hide the photo entirely until then.
      img.style.maxHeight = natural[i] > 0 ? (natural[i] * state.photoScale) + "px" : "";
    });
  }

  // Coalesced through rAF: resize fires continuously while a window is dragged,
  // and each pass above forces a synchronous reflow.
  let photoRAF = null;
  function scheduleRemeasure() {
    if (photoRAF) return;
    photoRAF = requestAnimationFrame(() => {
      photoRAF = null;
      applyPhotoScale(state.photoScale);
    });
  }

  // ---- card building ----
  function buildCard(t) {
    const card = document.createElement("article");
    card.className = "card" + (t.vacant ? " vacant" : "") + (t.teaser ? " teaser" : "");
    card.dataset.tap = String(t.tap);
    fillCard(card, t, true);
    return card;
  }

  function fillCard(card, t, force) {
    const prev = state.dataByTap.get(t.tap);
    const changed = (field) => force || !prev || prev[field] !== t[field];

    // Vacant <-> filled transition requires a structural refill - and so does a
    // teaser taking over, or leaving, a Slot whose `vacant` flag does not itself
    // change (a different pinned teaser replacing this one, or a real Tap
    // reclaiming the Slot the instant it stops being Vacant): either way the
    // border treatment and the inner markup both have to be redrawn.
    const wasVacant = prev ? prev.vacant : null;
    const wasTeaser = prev ? !!prev.teaser : false;
    if (force || wasVacant !== t.vacant || wasTeaser !== !!t.teaser) {
      card.classList.toggle("vacant", !!t.vacant);
      card.classList.toggle("teaser", !!t.teaser);
      card.innerHTML = t.vacant ? vacantInner(t) : filledInner(t);
      bindImage(card, t);
      measureMarquee(card.querySelector(".name"));
      measureMarquee(card.querySelector(".desc"));
      return;
    }
    if (t.vacant) return; // nothing else to update on a vacant card

    if (changed("name")) { setText(card, ".name .scroller", t.name); measureMarquee(card.querySelector(".name")); }
    if (changed("description")) { setText(card, ".desc .scroller", t.description || ""); measureMarquee(card.querySelector(".desc")); }
    if (changed("abv") || changed("abv_visible") || changed("abv_estimated")) {
      setText(card, '[data-stat="abv"] .v', abvText(t));
      setHidden(card, '[data-stat="abv"]', !t.abv_visible);
    }
    if (t.teaser) {
      // Only reachable when a different pinned teaser has taken over this
      // Slot without `teaser` itself flipping - see the structural-refill
      // note above and tapSignature's comment on the same case.
      if (changed("teaser_label")) {
        setText(card, ".ribbon", (t.teaser_label || "Coming up").toUpperCase());
      }
      if (changed("subtitle")) {
        setText(card, ".sub", t.subtitle || "");
        setHidden(card, ".sub", !t.subtitle);
      }
      if (changed("status_label")) {
        setText(card, ".status", t.status_label || "");
        setHidden(card, ".status", !t.status_label);
      }
    }
    if (changed("ibu") || changed("ibu_visible")) {
      setText(card, '[data-stat="ibu"] .v', fmtNum(t.ibu));
      setHidden(card, '[data-stat="ibu"]', !t.ibu_visible);
    }
    if (changed("og") || changed("og_visible")) {
      setText(card, '[data-stat="og"] .v', gravity(t.og));
      setHidden(card, '[data-stat="og"]', !t.og_visible);
    }
    if (changed("fg") || changed("fg_visible")) {
      setText(card, '[data-stat="fg"] .v', gravity(t.fg));
      setHidden(card, '[data-stat="fg"]', !t.fg_visible);
    }
    if (changed("ebc") || changed("ebc_visible")) {
      setText(card, '[data-stat="color"] .v', colorValue(t.ebc));
      setHidden(card, '[data-stat="color"]', !t.ebc_visible);
    }
    if (changed("color_hex") || changed("swatch_visible")) updateSwatch(card, t);
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

  // The '~' estimate marker (issue #39): a resolved answer off the wire
  // (t.abv_estimated), never derived here. Marking is unconditional on the
  // source of the number - a hydrometer reading on an unfinished beer is an
  // estimate too - so this file's only job is to prepend the glyph when told.
  function abvText(t) {
    const v = fmtNum(t.abv, "%");
    return t.abv_estimated && v !== "-" ? "~" + v : v;
  }

  function filledInner(t) {
    const s = state.settings;
    // The swatch's declared fallback for an Unknown Colour (the server sends
    // null rather than inventing one). Keep it in step with UNKNOWN_SWATCH_HEX
    // in app/colors.py - tests/test_frontend_constants.py fails if it drifts.
    const hex = t.color_hex || "#cccccc";
    const txt = t.text_color || "#f5f5f5";
    // Visibility arrives resolved, so this is a read, not a rule. The swatch and
    // the colour STAT are two separate answers off one operator toggle - an
    // override-only beer shows a swatch and no EBC number - and the board is
    // where that divergence is decided.
    const hAttr = (visible) => (visible ? "" : " hidden");
    const badge = s.show_source_badge
      ? `<span class="source-badge">${sourceLabel(t.source)}</span>` : "";
    // The teaser-only markup (issue #39): the ribbon and the subtitle/status
    // lines. Present in the DOM only for a teaser card - never rendered, not
    // merely hidden, on an ordinary filled Tap card - so a Tap's grid keeps
    // its plain 3-row layout (head/desc/foot) and only `.card.teaser` needs
    // the extra row `display.css` gives the meta block. Once a card IS a
    // teaser, `fillCard`'s diff path can still flip `.sub`/`.status`
    // individually via `hidden` without a structural rebuild - see there.
    const ribbon = t.teaser
      ? `<div class="ribbon">${esc((t.teaser_label || "Coming up").toUpperCase())}</div>` : "";
    const meta = t.teaser
      ? `<div class="teaser-meta">
          <p class="sub"${t.subtitle ? "" : " hidden"}>${esc(t.subtitle || "")}</p>
          <p class="status"${t.status_label ? "" : " hidden"}>${esc(t.status_label || "")}</p>
        </div>` : "";
    return `
      ${ribbon}
      <div class="card-head">
        <div class="tap-num">${t.tap}</div>
        <h2 class="name"><span class="scroller">${esc(t.name || "Tap " + t.tap)}</span></h2>
        <div class="swatch" style="background:${hex};color:${txt}"${hAttr(t.swatch_visible)}></div>
      </div>
      ${meta}
      <p class="desc"><span class="scroller">${esc(t.description || "")}</span></p>
      <div class="card-foot">
        <img class="thumb" alt="" src="${esc(t.image_url || "/img/placeholder")}">
        <div class="stats">
          <div class="stat" data-stat="abv"${hAttr(t.abv_visible)}><span class="v">${abvText(t)}</span><span class="k">ABV</span></div>
          <div class="stat" data-stat="ibu"${hAttr(t.ibu_visible)}><span class="v">${fmtNum(t.ibu)}</span><span class="k">IBU</span></div>
          <div class="stat" data-stat="og"${hAttr(t.og_visible)}><span class="v">${gravity(t.og)}</span><span class="k">OG</span></div>
          <div class="stat" data-stat="fg"${hAttr(t.fg_visible)}><span class="v">${gravity(t.fg)}</span><span class="k">FG</span></div>
          <div class="stat" data-stat="color"${hAttr(t.ebc_visible)}><span class="v">${colorValue(t.ebc)}</span><span class="k">${colorLabel()}</span></div>
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
    // Same declared Unknown fallback as filledInner - see the note there. The
    // hidden state is the board's resolved answer, so the swatch rule exists in
    // one place rather than being written out here a second time.
    sw.style.background = t.color_hex || "#cccccc";
    sw.style.color = t.text_color || "#f5f5f5";
    sw.hidden = !t.swatch_visible;
  }

  function bindImage(card, t) {
    const img = card.querySelector(".thumb");
    if (!img) return;
    // A photo arriving over HTTP lands after the card is in the DOM, so any cap
    // measured before it decoded was taken against an empty box. Re-measure once
    // it is there. (The placeholder swap below fires this again, which is what we
    // want: the placeholder has its own proportions.)
    img.addEventListener("load", scheduleRemeasure);
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
    // Every card node was just replaced, so every inline photo cap went with it.
    scheduleRemeasure();
    state.hasRendered = true;
  }

  function diffUpdate(taps) {
    let changed = false;
    taps.forEach((t) => {
      const card = state.cardEls.get(t.tap);
      if (!card) return;
      const prev = state.dataByTap.get(t.tap);
      if (prev && tapSignature(prev) === tapSignature(t)) return; // unchanged
      fillCard(card, t, false);
      state.dataByTap.set(t.tap, t);
      changed = true;
    });
    // A vacant<->filled flip rebuilds a card's inner HTML, and a longer beer name
    // or description re-divides the card, so any change can invalidate the caps.
    if (changed) scheduleRemeasure();
  }

  function showPage(idx) {
    const pages = stage.querySelectorAll(".page");
    pages.forEach((p, i) => p.classList.toggle("active", i === idx));
    state.currentPage = idx;
    renderDots();
    // Pages are stacked and laid out together (only opacity differs), so a flip
    // does not resize anything today. Re-measuring anyway is one rAF per 30s and
    // keeps the caps correct if that ever changes to a display-toggling page.
    scheduleRemeasure();
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

  // Backward companion to nextPage(). Both route through goToPage() so the
  // rotation timer is restarted in exactly one place, however navigation
  // started. Adding the page count before the modulo keeps the index positive
  // when stepping back off page 0, which JS's % would otherwise return as -1.
  function prevPage() {
    if (state.pages.length <= 1) return;
    goToPage((state.currentPage - 1 + state.pages.length) % state.pages.length);
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

  /* ---- touch navigation: a horizontal swipe flips a page ----

     Bound to the board, not the stage: the stage is only the flexible grid row
     and shrinks whenever the venue header, the dots or the ticker appear, so on
     a touch TV a finger landing near the top or bottom edge would miss it. The
     board is the whole visible screen, which is what an operator actually swipes.

     The flip is instant on release, with no drag-follow: pages are stacked in
     one layout box and cross-faded (only opacity differs), so there is no
     horizontal filmstrip to translate mid-gesture. */
  const swipe = { id: null, x: 0, y: 0 };
  function abandonSwipe() { swipe.id = null; }

  if (board) {
    board.addEventListener("touchstart", (e) => {
      // Single touch point only. A second finger means a pinch or a two-finger
      // scroll, so abandon rather than guess which one is steering.
      if (e.touches.length !== 1) { abandonSwipe(); return; }
      const t = e.changedTouches[0];
      swipe.id = t.identifier;
      swipe.x = t.clientX;
      swipe.y = t.clientY;
    }, { passive: true });

    // A gesture the browser or the OS takes over (or a pointer that leaves the
    // surface) never becomes a page change: the current page simply stands.
    board.addEventListener("touchcancel", abandonSwipe, { passive: true });

    board.addEventListener("touchend", (e) => {
      if (swipe.id === null) return;
      // Match by identifier so a stray finger's touchend cannot end this gesture.
      let t = null;
      for (let i = 0; i < e.changedTouches.length; i++) {
        if (e.changedTouches[i].identifier === swipe.id) { t = e.changedTouches[i]; break; }
      }
      if (!t) return;
      const dx = t.clientX - swipe.x;
      const dy = t.clientY - swipe.y;
      abandonSwipe();
      // Two separate guards, both needed. The distance test keeps a stationary
      // tap a tap, so the page dots stay independently clickable. The dominance
      // test stops a mostly-vertical drag that happened to wander past the
      // threshold from flipping the page sideways.
      if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) <= Math.abs(dy)) return;
      // Swipe left (content dragged towards the left edge) advances, matching
      // every mobile carousel. nextPage/prevPage carry the single-page guard and
      // the wrap-around, and restart the rotation timer via goToPage().
      if (dx < 0) nextPage(); else prevPage();
    }, { passive: true });
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
      console.warn("board poll failed:", err);
      if (!state.hasRendered) bootError.hidden = false;
    }
  }

  function applyBoard(board) {
    // A Vacant Slot's pinned teaser (issue #38) is substituted in before
    // anything below looks at `board.taps` - layout, paging, the diff and every
    // signature all key off `t.tap`/`t.vacant`, so folding the substitution in
    // here once means none of them need to know teasers exist at all. `upcoming`
    // is absent from the payload entirely with the feature off, and
    // `withTeasers` treats that exactly like an empty list.
    board = { ...board, taps: withTeasers(board) };

    applyTheme(board.theme);
    applyCardScales(board);

    // Only the settings this file still has a use for. Visibility is not among
    // them: it reaches us per tap, already resolved.
    state.settings = {
      color_unit: board.color_unit || "ebc",
      show_source_badge: board.show_source_badge === true,
      tap_image_scale: Number(board.tap_image_scale) || 1,
      tap_text_scale: Number(board.tap_text_scale) || 1,
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
    // The photo caps are absolute px, so they are wrong the moment the viewport
    // changes: without this, shrinking the window leaves the photos at the height
    // they were given for the old layout until a preset is saved again.
    scheduleRemeasure();
  });

  // A resize is not the only way the cards change size: the venue logo appearing
  // (or its height changing) resizes the stage without touching the window. The
  // observer catches both, and on a tablet it also covers the orientation change
  // that settles after `orientationchange` has already fired.
  if (typeof ResizeObserver === "function" && stage) {
    new ResizeObserver(scheduleRemeasure).observe(stage);
  }

  // ---- boot ----
  let pollTimer = null;
  async function pollLoop() {
    await poll();
    pollTimer = setTimeout(pollLoop, state.hasRendered ? POLL_MS : FAST_RETRY_MS);
  }
  pollLoop();
  restartCarousel();
})();
