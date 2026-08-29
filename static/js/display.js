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
    // The one cadence driving every upcoming animation (issue #40) - needed
    // before the first board arrives because the cross-fade's own timer
    // starts at boot, same as the carousel's rotation_seconds above.
    upcoming_interval_seconds: 20,
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
    lastBoard: null,        // the most recent raw board, for the cross-fade scheduler (issue #40)
  };

  // Which carousel page is the on-deck page (issue #41), or -1 when it is
  // absent (disabled, or nothing to carry). The deck page is an ordinary
  // carousel page with no scheduling of its own - the rotation timer shows it
  // like any other - so this index exists only so the half-board panel can
  // refuse to stack on top of it (see panelTick/showPage). Module-scoped for
  // the same reason the cross-fade's own timers below are: a layout concern,
  // not board data.
  let deckPageIndex = -1;

  // The half-board panel's own state (issue #42). An overlay element, not a
  // page - it floats over whichever page is active rather than joining
  // state.pages - so it is kept module-scoped for the same reason
  // `deckPageIndex` above is: purely a scheduling/layout concern of this
  // surface. `panelEl` is null whenever there is nothing to carry, which is
  // what makes "not rendered at all" (rather than rendered empty) automatic:
  // panelTick() below is a no-op against a null element.
  let panelEl = null;
  let panelMultiple = 2;        // the operator's multiple of the shared interval
  let panelTickCounter = 0;     // counts shared-interval ticks; the panel's own cadence
  let panelHoldTimer = null;
  let panelFadeTimer = null;

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

  // The subset of a resolved teaser's fields that make it drawable as a card,
  // shared by the pinned-Slot substitution below and the cross-fade overlay
  // (issue #40) - one function so the two paths cannot drift apart. `label`
  // is the board-level ribbon text (issue #39): one label for every teaser,
  // not a per-teaser answer, so callers read it once rather than carrying it
  // on each entry in `board.upcoming`.
  function teaserCardFields(u, label) {
    return {
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
    // Absent whenever `upcoming` itself is (the feature is off), which never
    // reaches this branch anyway since `pinned` would then be empty.
    const label = board.upcoming_label || "Coming up";
    return (board.taps || []).map((t) => {
      if (!t.vacant) return t;
      const u = pinned.get(t.tap);
      if (!u) return t;
      return { ...t, vacant: false, teaser: true, ...teaserCardFields(u, label) };
    });
  }

  // Whether a card carries the meta block that hosts `.sub` and `.status`.
  // A teaser always does (issue #39). An ordinary Tap card does whenever the
  // board resolved a status marker for it (issue #45) - a pouring beer that is
  // still conditioning - and never otherwise, so a Tap with no marker keeps its
  // plain 3-row grid. `status_label` is the board's already-resolved answer;
  // this file only asks whether there is one, never whether to show it.
  function hasMeta(t) {
    return !!(t.teaser || t.status_label);
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
      // `status_label` is not teaser-only any more (issue #45) - it is also a
      // Tap card's own conditioning marker, which appears, changes or clears
      // with nothing else about the Tap moving, so the diff path has to see it.
      t.teaser_label, t.status_label, t.subtitle, t.abv_estimated,
    ].join("|");
  }

  function layoutSignature(pages) {
    return pages.map((p) => p.join(",")).join(";");
  }

  // The teasers ANY overflow surface may carry: `on_surfaces` is already the
  // resolved answer (board.py's resolve_upcoming) - this file never
  // re-derives it from the scope Setting, which it never even sees
  // (CLAUDE.md). Both the on-deck page and the half-board panel read the
  // identical set through this one function, because they carry the same
  // surface set (issue #42) - there is no per-surface scope, only a
  // per-surface enable toggle and multiple.
  function overflowTeasers(board) {
    return board.upcoming ? board.upcoming.filter((u) => u.on_surfaces) : [];
  }

  // The teasers the on-deck page carries (issue #41): empty whenever the page
  // itself is disabled, so a caller never has to check the toggle twice.
  function deckTeasers(board) {
    return board.upcoming_deck_enabled ? overflowTeasers(board) : [];
  }

  // The teasers the half-board panel carries (issue #42): same shape as
  // deckTeasers above, gated on the panel's own toggle instead.
  function panelTeasers(board) {
    return board.upcoming_panel_enabled ? overflowTeasers(board) : [];
  }

  // A change signature for the deck page's own content, folded into the
  // layout key in applyBoard(): the deck page is not itself a Tap page, so
  // layoutSignature()/settingsSignature() alone would miss a teaser being
  // added, dropped, or edited and leave the page stale until something else
  // happened to force a full re-render.
  function deckSignature(board) {
    return deckTeasers(board).map((u) => [
      u.batch_id, u.name, u.abv, u.ibu, u.ebc, u.og, u.fg, u.color_hex,
      u.abv_visible, u.ibu_visible, u.ebc_visible, u.og_visible, u.fg_visible,
      u.swatch_visible, u.description, u.image_url, u.status_label,
      u.subtitle, u.abv_estimated,
    ].join(",")).join(";");
  }

  // The half-board panel's own change signature (issue #42), the same
  // shape and the same reason as deckSignature above: the panel is not a
  // Tap and is not itself a carousel page, so a teaser being added, dropped
  // or edited would otherwise go unnoticed by layoutSignature() /
  // settingsSignature() / deckSignature() alone and leave the panel stale.
  function panelSignature(board) {
    return panelTeasers(board).map((u) => [
      u.batch_id, u.name, u.abv, u.ibu, u.ebc, u.og, u.fg, u.color_hex,
      u.abv_visible, u.ibu_visible, u.ebc_visible, u.og_visible, u.fg_visible,
      u.swatch_visible, u.description, u.image_url, u.status_label,
      u.subtitle, u.abv_estimated,
    ].join(",")).join(";");
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
    //
    // A Tap card gaining or losing its status marker (issue #45) is the same
    // kind of change: the meta block that hosts `.status` is not merely hidden
    // when there is no marker, it is not in the DOM at all, so the diff branch
    // below would have nothing to write into. Once a card HAS the block, a
    // marker changing word is handled in place.
    const wasVacant = prev ? prev.vacant : null;
    const wasTeaser = prev ? !!prev.teaser : false;
    const hadMeta = prev ? hasMeta(prev) : false;
    if (force || wasVacant !== t.vacant || wasTeaser !== !!t.teaser
        || hadMeta !== hasMeta(t)) {
      card.classList.toggle("vacant", !!t.vacant);
      card.classList.toggle("teaser", !!t.teaser);
      // The extra grid row the meta block needs, on a teaser and on a marked
      // Tap card alike - see display.css's .card.has-meta.
      card.classList.toggle("has-meta", !t.vacant && hasMeta(t));
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
    }
    // Deliberately OUTSIDE the teaser branch (issue #45): a Tap card carries a
    // status marker too. Reachable only while the meta block already exists -
    // gaining or losing it forced a structural refill above - so this only ever
    // rewrites one marker word into another.
    if (changed("status_label")) {
      setText(card, ".status", t.status_label || "");
      setHidden(card, ".status", !t.status_label);
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
    // The ribbon stays teaser-only (issue #39). The meta block below it is
    // shared: a teaser always has one, and an ordinary Tap card gets one when
    // the board resolved a status marker for it (issue #45, `hasMeta`). It is
    // not rendered at all otherwise - not merely hidden - so an unmarked Tap
    // keeps its plain 3-row layout (head/desc/foot) and only a card with the
    // block wears the extra row `display.css` gives `.card.has-meta`. Once a
    // card HAS the block, `fillCard`'s diff path can flip `.sub`/`.status`
    // individually via `hidden` without a structural rebuild - see there.
    //
    // `.sub` is left in the markup unconditionally rather than gated on
    // `t.teaser`: a Tap card has no subtitle, so it renders hidden and empty,
    // and keeping one shape means the diff path writes into the same DOM on
    // both kinds of card.
    const ribbon = t.teaser
      ? `<div class="ribbon">${esc((t.teaser_label || "Coming up").toUpperCase())}</div>` : "";
    const meta = hasMeta(t)
      ? `<div class="card-meta">
          <p class="sub"${t.subtitle ? "" : " hidden"}>${esc(t.subtitle || "")}</p>
          <p class="status"${t.status_label ? "" : " hidden"}>${esc(t.status_label || "")}</p>
        </div>` : "";
    return `
      ${ribbon}
      <div class="card-head">
        ${Number.isInteger(t.tap) ? `<div class="tap-num">${esc(t.tap)}</div>` : ""}
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
        <div class="tap-num">${esc(t.tap)}</div>
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
    // Every card on the stage, not just the Tap cards in state.cardEls: the
    // deck page's and the panel's cards are deliberately kept out of that map
    // (it is the cross-fade/diff lookup, keyed by real Slot numbers), but
    // their long names and descriptions still need the marquee - they are the
    // overflow surfaces, where clipping loses exactly the text the surface
    // exists to show.
    stage.querySelectorAll(".card").forEach((card) => {
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
    // A structural rebuild replaces every card element (and wipes `stage`'s
    // own children, which is where an in-flight overlay lives), so any
    // cross-fade in progress can no longer be trusted to be positioned over
    // anything real. Ending it here - rather than waiting for its own hold
    // timer - is what keeps a poll from leaving the interlock stuck "busy"
    // for the rest of that teaser's hold. `crossFadeTurn` (the shared-Slot
    // alternation) is untouched: a poll must never reset it, only the overlays.
    crossFadeForceEnd();
    panelForceEnd();
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

    // The on-deck page (issue #41): one more page, joining the carousel like
    // any other - the rotation timer, the dots and manual navigation all key
    // off `state.pages.length` alone and need no special case, and it takes
    // its turns in the ordinary rotation rather than on a schedule of its
    // own. Its own tap list is `[]` (it carries teasers, not Taps), which is
    // what makes the cross-fade's existing page guard in showPage() already
    // correct here with no change: an empty list can never contain a bound
    // Slot number. With nothing to carry the page is simply not added at all
    // (issue #41's "not rendered", not "rendered empty") - `deckPageIndex`
    // stays -1 and the panel's no-stack guard below never fires.
    const deckList = deckTeasers(board);
    if (deckList.length) {
      const label = board.upcoming_label || "Coming up";
      const page = document.createElement("div");
      page.className = "page";
      // The per-count grid layouts are tuned up to MAX_CARDS_PER_PAGE, same
      // as a Tap page; past it the CSS falls back to a single column and a
      // card collapses to a sliver. max_upcoming_previews allows up to 20, so
      // the surface takes the first cards of the already-ordered queue (most
      // ready first) rather than rendering an unreadable wall.
      const deckShown = deckList.slice(0, MAX_CARDS_PER_PAGE);
      page.dataset.count = String(deckShown.length);
      deckShown.forEach((u, i) => {
        // A synthetic, string `tap` id: deck cards are never looked up by tap
        // number (that map is `state.cardEls`, used only for cross-fade cell
        // lookups and the Tap diff path) and a string can never collide with
        // a real integer tap number.
        const card = buildCard({
          tap: "deck-" + (u.batch_id != null ? u.batch_id : i),
          vacant: false, teaser: true, ...teaserCardFields(u, label),
        });
        page.appendChild(card);
      });
      stage.appendChild(page);
      state.pages.push([]);
      deckPageIndex = state.pages.length - 1;
    } else {
      deckPageIndex = -1;
    }

    // The half-board panel (issue #42): an overlay appended directly to the
    // stage, not a page - it floats over the bottom half of whichever page is
    // active rather than joining the carousel. With nothing to carry it is
    // never appended at all (`panelEl` stays null), matching the on-deck
    // page's own "not rendered" contract just above rather than being built
    // and left invisible.
    const panelList = panelTeasers(board);
    if (panelList.length) {
      const label = board.upcoming_label || "Coming up";
      const panel = document.createElement("div");
      panel.className = "upcoming-panel";
      // Same ceiling as the deck page above: a bottom-half strip past eight
      // cards is unreadable on a TV, so the panel carries the head of the
      // ordered queue too.
      const panelShown = panelList.slice(0, MAX_CARDS_PER_PAGE);
      panel.style.setProperty("--panel-count", String(panelShown.length));
      panelShown.forEach((u, i) => {
        // Same synthetic string `tap` id scheme as the deck page's cards:
        // never looked up by tap number, and a string can never collide with
        // a real integer tap number.
        const card = buildCard({
          tap: "panel-" + (u.batch_id != null ? u.batch_id : i),
          vacant: false, teaser: true, ...teaserCardFields(u, label),
        });
        panel.appendChild(card);
      });
      stage.appendChild(panel);
      panelEl = panel;
    } else {
      panelEl = null;
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
    // The cross-fade's page guard (issue #40): leaving the page that carries
    // the in-flight overlays' taps pulls them immediately, with no fade-out -
    // this is reached by manual dot navigation (goToPage) exactly as it is by
    // the carousel timer, which is the whole reason the guard lives here
    // rather than in the scheduler that started the overlays. The group was
    // built against ONE page's cells, so any overlay off the new page means
    // the whole group goes - it lives and dies together.
    if (crossFadeOverlays.length) {
      const activePage = state.pages[idx] || [];
      if (crossFadeOverlays.some((o) => !activePage.includes(o.tap))) crossFadeForceEnd();
    }
    // The panel's own page guard (issue #4 close-out): the panel and the
    // on-deck page carry the same teaser set, so the two must never stack.
    // panelTick() skips a turn that would START over the deck page; this
    // covers the other direction - the carousel (or a dot click) LANDING on
    // the deck page while the panel is up. Same reasoning as the cross-fade
    // guard above: navigation does not go through the scheduler, so the
    // guard has to live here.
    if (deckPageIndex >= 0 && idx === deckPageIndex) panelHide();
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

  /* ---- the in-place cross-fade baseline (issue #40) ----

     One clock drives every scheduled upcoming animation - this cross-fade
     and the half-board panel (#42) - which is why the interlock and the
     scheduling shape below are built as shared infrastructure rather than
     something private to the cross-fade. This is the ONE scheduler; a later
     surface joins `upcomingBusy` and the same `crossFadeIntervalMs`/multiple
     pattern instead of building a second one. (The on-deck page is not on
     this clock at all: it rides the ordinary carousel rotation.)

     `pinned`, `cross_fade` and (later) `on_surfaces` are resolved answers off
     the wire (board.py) - this file never re-derives whether a teaser may be
     shown, only executes the schedule it is told to run.

     Two guards, and they catch different things:
     - `upcomingBusy` (the interlock): nothing starts while something else is
       up. A turn arriving while busy is skipped, not queued - see
       crossFadeTick().
     - the PAGE guard (crossFadeOverlays vs. the active page's tap list):
       the interlock alone does not stop an overlay landing on the wrong
       page, because manual dot navigation changes the page without going
       through this scheduler at all. Found in the issue #4 display
       prototype (since deleted, per the prototype convention): a timer
       fired mid-navigation and drew its teaser over a page that did not
       carry the bound tap. See showPage() below. */

  // How long a teaser stays up, derived from the cadence rather than a
  // separate Setting (CLAUDE.md): about 58% of the gap, floored at 1.5s so a
  // fast cadence never produces a flash nobody can read.
  function holdMs(intervalSeconds) {
    return Math.max(1500, Math.round((Number(intervalSeconds) || 20) * 1000 * 0.58));
  }

  // The interlock every upcoming-beer animation shares. Only the cross-fade
  // exists in this ticket, but the flag lives here (not private to it) so a
  // later surface can read and set it without a second interlock appearing.
  let upcomingBusy = false;

  let crossFadeTimer = null;
  let crossFadeIntervalMs = DEFAULT_SETTINGS.upcoming_interval_seconds * 1000;
  let crossFadeTurn = 0;           // advances per shown turn; alternates teasers sharing a Slot
  let crossFadeOverlays = [];      // in-flight overlays, one per covered Slot: {el, tap}
  let crossFadeHoldTimer = null;
  let crossFadeFadeTimer = null;

  function crossFadePendingTimersClear() {
    if (crossFadeHoldTimer) { clearTimeout(crossFadeHoldTimer); crossFadeHoldTimer = null; }
    if (crossFadeFadeTimer) { clearTimeout(crossFadeFadeTimer); crossFadeFadeTimer = null; }
  }

  // Ends an in-flight cross-fade immediately, with no fade-out: used when the
  // overlays can no longer be trusted to be in the right place (a structural
  // re-render replaced the card elements, or the tap page they cover is no
  // longer showing) rather than when their own hold naturally expires. The
  // whole group lives and dies together - there is never a turn with some
  // Slots covered and others already released.
  function crossFadeForceEnd() {
    crossFadePendingTimersClear();
    crossFadeOverlays.forEach((o) => o.el.remove());
    crossFadeOverlays = [];
    upcomingBusy = false;
  }

  // Bound teasers the baseline may show - `cross_fade` is already the
  // resolved answer (board.py's resolve_upcoming); this file never re-derives
  // "occupied" or whether rotation is allowed from the Setting that decides
  // it - it never even sees that Setting (CLAUDE.md).
  function crossFadeCandidates() {
    const b = state.lastBoard;
    return b && b.upcoming ? b.upcoming.filter((u) => u.cross_fade && u.slot != null) : [];
  }

  function crossFadeTick() {
    if (upcomingBusy) return;              // interlock: skip this turn, not queue it
    const candidates = crossFadeCandidates();
    if (!candidates.length) return;
    // Every bound Slot on the ACTIVE page gets its overlay in the same turn,
    // fading in together and out together. The baseline shipped as one
    // teaser per tick, cycling, and on a board with several upcoming beers
    // that read as a teaser always coming or going somewhere - restless
    // rather than informative. One synchronised group reads as a single
    // event: "here is what is coming", then the board again.
    //
    // The page guard is folded into the grouping. An inactive carousel page
    // is still laid out (only faded to opacity 0), so without it an overlay
    // would land in the right PLACE over the wrong PAGE. Checking the active
    // page's own tap list keeps this correct for pagination, and covers the
    // on-deck page for free - its tap list is empty, so nothing can group
    // onto it. A Slot on another page simply waits for the carousel to
    // bring its page around, exactly as before.
    const activePage = state.pages[state.currentPage] || [];
    const bySlot = new Map();
    candidates.forEach((u) => {
      if (!activePage.includes(u.slot)) return;
      if (!bySlot.has(u.slot)) bySlot.set(u.slot, []);
      bySlot.get(u.slot).push(u);
    });
    if (!bySlot.size) return;
    // Two Batches may claim one occupied Slot ("both tease" - the FAQ's
    // contract), and they cannot stack on one cell, so teasers sharing a
    // Slot alternate across turns. That per-Slot pick is all that survives
    // of the old cycling; a Slot with one teaser shows it every turn.
    const turn = crossFadeTurn++;
    const shown = [];
    bySlot.forEach((slotTeasers, slot) => {
      const cell = state.cardEls.get(slot);
      if (!cell) return;
      const teaser = slotTeasers[turn % slotTeasers.length];
      shown.push({ el: crossFadeBuildOverlay(cell, teaser), tap: slot });
    });
    if (!shown.length) return;
    crossFadeOverlays = shown;
    upcomingBusy = true;
    // One rAF for the whole group, so every Slot's fade starts on the same
    // frame; the shared hold and fade timers below end them together too.
    requestAnimationFrame(() => shown.forEach((o) => { o.el.style.opacity = "1"; }));
    crossFadeHoldTimer = setTimeout(() => {
      shown.forEach((o) => { o.el.style.opacity = "0"; });
      crossFadeFadeTimer = setTimeout(crossFadeForceEnd, 500);
    }, holdMs(state.settings.upcoming_interval_seconds));
  }

  // Builds and mounts one Slot's overlay, hidden: the caller fades the whole
  // group in with a single rAF and owns the shared hold/fade timers.
  function crossFadeBuildOverlay(cell, teaser) {
    const stageRect = stage.getBoundingClientRect();
    const rect = cell.getBoundingClientRect();
    const label = (state.lastBoard && state.lastBoard.upcoming_label) || "Coming up";
    const overlay = document.createElement("div");
    overlay.className = "cross-fade-overlay";
    overlay.style.cssText =
      "position:absolute;display:grid;pointer-events:none;z-index:5;" +
      "opacity:0;transition:opacity 500ms ease;" +
      `left:${rect.left - stageRect.left}px;top:${rect.top - stageRect.top}px;` +
      `width:${rect.width}px;height:${rect.height}px;`;
    const card = buildCard({
      tap: teaser.slot, vacant: false, teaser: true,
      ...teaserCardFields(teaser, label),
    });
    overlay.appendChild(card);
    stage.appendChild(overlay);
    // Measured here, not by measureAllMarquees: the overlay exists only
    // between shows, so a long name or description on the teaser gets its
    // marquee for exactly the hold it is on screen.
    measureMarquee(card.querySelector(".name"));
    measureMarquee(card.querySelector(".desc"));
    return overlay;
  }

  /* ---- the half-board panel's own turn (issue #42) ----

     Built on the shared scheduler #40 set up: its own tick counter against
     its own multiple, the same `upcomingBusy` interlock (a turn arriving
     while the cross-fade is up is skipped, not queued), and the same derived
     `holdMs()`. The panel's turn is an overlay fade - the same shape as the
     cross-fade's own show/hide - because the panel floats over the board
     rather than replacing what is on screen.

     The on-deck page used to take scheduled turns here too (jump to it,
     hold, jump back); that flicked over instead of flowing, so it now rides
     the ordinary carousel rotation and the panel is the only surface with a
     cadence of its own (issue #4 close-out). What survives of the deck page
     in this scheduler is the no-stack rule below: both carry the same teaser
     set, so a panel turn that would land on top of the deck page is skipped,
     not queued - the same shape as the interlock. */

  function panelTick() {
    panelTickCounter++;
    if (!panelEl) return;                              // disabled, or nothing to carry
    if (panelTickCounter % Math.max(1, panelMultiple) !== 0) return;
    if (upcomingBusy) return;                           // interlock: skip this turn, not queue it
    // The no-stack rule: never open the panel over the on-deck page - the
    // page already shows everything the panel would. showPage() covers the
    // other direction (navigating onto the deck page mid-hold).
    if (deckPageIndex >= 0 && state.currentPage === deckPageIndex) return;
    upcomingBusy = true;
    panelEl.classList.add("show");
    panelHoldTimer = setTimeout(() => {
      panelHoldTimer = null;
      panelEl.classList.remove("show");
      panelFadeTimer = setTimeout(() => { panelFadeTimer = null; upcomingBusy = false; }, 500);
    }, holdMs(state.settings.upcoming_interval_seconds));
  }

  // Ends an in-flight panel turn immediately, keeping the element for its
  // next turn: used by showPage()'s no-stack guard, where the panel node is
  // still real - only the page underneath it changed. If the panel is not
  // up, the timers are already null and `upcomingBusy` belongs to whoever
  // holds it (possibly the cross-fade), so there is nothing to release: the
  // interlock guarantees the panel and the cross-fade are never up at once,
  // which is what makes resetting it safe when the timers say the panel WAS up.
  function panelHide() {
    const wasUp = panelHoldTimer !== null || panelFadeTimer !== null;
    if (panelHoldTimer) { clearTimeout(panelHoldTimer); panelHoldTimer = null; }
    if (panelFadeTimer) { clearTimeout(panelFadeTimer); panelFadeTimer = null; }
    if (panelEl) panelEl.classList.remove("show");
    if (wasUp) upcomingBusy = false;
  }

  // Ends an in-flight panel turn AND forgets the element: used exactly when
  // crossFadeForceEnd() is - a structural re-render is about to replace (or
  // remove) the panel element itself, so any pending hide/release would
  // otherwise fire against a node no longer on screen.
  function panelForceEnd() {
    panelHide();
    panelEl = null;
  }

  // The one shared interval (issue #40/#42): the panel's turn is driven off
  // this same tick rather than a timer of its own.
  function upcomingTick() {
    // The panel dispatches BEFORE the cross-fade, deliberately. The busy
    // window (holdMs + fade) is shorter than every legal interval, so
    // upcomingBusy is always free at a tick boundary - whichever consumer
    // runs first wins the tick. The cross-fade wants a turn every tick; the
    // panel wants one only every Nth. Cross-fade-first therefore starves the
    // panel on any board where it has a candidate, and the multiplier exists
    // precisely to hand the panel those Nth ticks while the baseline keeps
    // the rest.
    panelTick();
    crossFadeTick();
  }

  function setUpcomingInterval(seconds) {
    const ms = Math.max(5, Number(seconds) || 20) * 1000;
    if (ms === crossFadeIntervalMs && crossFadeTimer) return;
    crossFadeIntervalMs = ms;
    if (crossFadeTimer) clearInterval(crossFadeTimer);
    crossFadeTimer = setInterval(upcomingTick, crossFadeIntervalMs);
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
    // The cross-fade scheduler reads `upcoming` and `upcoming_label` off the
    // raw board directly (state.lastBoard), rather than off the
    // teaser-substituted `taps` above - it needs the full Upcoming Beer list,
    // not just the ones that happen to be pinned into a Vacant Slot.
    state.lastBoard = board;

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
      // Absent whenever `upcoming` itself is (the feature off); the default
      // just keeps the scheduler's own timer sane until a board says
      // otherwise - it never fires anything with an empty candidate list.
      upcoming_interval_seconds: Number(board.upcoming_interval_seconds) ||
        DEFAULT_SETTINGS.upcoming_interval_seconds,
    };

    setRotation(state.settings.rotation_seconds);
    setUpcomingInterval(state.settings.upcoming_interval_seconds);
    updateVenueHeader(board);

    // The half-board panel's own multiple (issue #42): updated on every
    // poll, independent of whether this poll triggers a full re-render, so a
    // changed multiple takes effect on its very next tick rather than
    // waiting for some unrelated layout change to force a rebuild. Not
    // re-clamped here: the config store is the single enforcement point for
    // the 1..6 bound, and the board only ever sends coerced values - a second
    // clamp in this file would be a copy of that rule waiting to drift.
    panelMultiple = Number(board.upcoming_panel_multiple) || 2;

    const taps = visibleTaps(board);
    const pages = chunk(taps.map((t) => t.tap), pageSize());
    // The deck page's own content is folded into the key: it is not a Tap
    // page, so a teaser being added, dropped or edited would otherwise go
    // unnoticed by layoutSignature()/settingsSignature() alone and leave the
    // page stale.
    const key = layoutSignature(pages) + "#" + settingsSignature(state.settings) +
      "#deck:" + deckSignature(board) + "#panel:" + panelSignature(board);
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
  setUpcomingInterval(state.settings.upcoming_interval_seconds);
})();
