"""FastAPI application: TV display, admin interface, board API, asset serving.

Routes
------
Public display:
  GET  /                -> TV display page (fully self-contained, local assets)
  GET  /api/board       -> fully-resolved board JSON (frontend never parses md)
  GET  /api/preview-color -> computed swatch colour for the admin live preview
  GET  /img/{filename}  -> tap image from /data/taps (path-sanitised)
  GET  /img/upcoming/{filename} -> upcoming-beer image from /data/upcoming (path-sanitised)
  GET  /img/placeholder -> fallback image
  GET  /healthz         -> lightweight healthcheck

Admin (session-protected):
  GET  /admin/login     -> login form
  POST /admin/login     -> authenticate (rate-limited)
  POST /admin/logout    -> clear session
  GET  /admin           -> admin dashboard
  POST /admin/settings  -> save settings
  POST /admin/override/{tap} -> save / clear a manual override (+ image upload)
  POST /admin/sync      -> trigger a sync now
  GET  /admin/snapshot  -> stream a Snapshot of the data directory as a zip
  POST /admin/snapshot/stage  -> upload a Snapshot and validate it
  POST /admin/snapshot/import -> restore the staged Snapshot
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from . import admin_ops, auth, snapshot, tap_store as taps
from .atomic import JOB_LOCK, atomic_write_bytes, safe_unlink
from .beer import Beer, TapPresentation
from .beer_glass import DEFAULT_GLASS, GLASS_TYPES, beer_glass_svg
from .board import build_board
from .brewfather import run_sync
from .colors import (
    UNKNOWN_SWATCH_HEX,
    display_color_to_ebc,
    ebc_to_srm,
    parse_saturation,
    resolve_color,
    text_color_for,
)
from .config_store import (
    MAX_UPCOMING_LABEL_LEN,
    SETTINGS_BOUNDS,
    UPCOMING_LABEL_PRESETS,
    apply_settings,
    brewfather_credentials,
    load_config,
    update_config,
)
from .theme import DEFAULT_THEME, THEME_FIELD_LABELS, THEME_KEYS, THEMES
from .update_check import (check_for_updates, current_version,
                           is_update_available, update_state)
from .demo import maybe_seed_demo
from . import persistence
from .paths import (
    DATA_DIR,
    STATIC_DIR,
    TAPS_DIR,
    TEMPLATES_DIR,
    UPCOMING_DIR,
    VENUE_LOGO_EXTS,
    ensure_dirs,
    placeholder_path,
    venue_logo_path,
)
from .scheduler import shutdown_scheduler, start_scheduler
from .status_store import load_status, migrate_legacy_status
from .timezone import iso_now

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("taplist.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: bootstrap data, seed demo, run an initial sync, schedule jobs."""
    ensure_dirs()
    # Durability checks run here - after the tree exists, and *before* demo
    # seeding, so a demo seed can never be mistaken for operator data that
    # survived. The verdict is computed once and read by the admin page.
    persistence.run_startup_checks()
    load_config()  # first-run bootstrap of config.json
    # One-time carry of the Status fields out of a pre-split config.json. Runs
    # before the scheduler so no job can write status.json ahead of it, which is
    # what lets the migration treat an existing status.json as authoritative.
    migrate_legacy_status()
    _ensure_local_placeholder()
    maybe_seed_demo()
    if auth.demo_admin_open():
        log.warning(
            "DEMO_MODE with no ADMIN_PASSWORD: /admin is OPEN (no login required). "
            "Set ADMIN_PASSWORD before exposing this box to anyone."
        )
    start_scheduler()
    # Kick an immediate sync in the background so the box is fresh on boot
    # without blocking startup. (No-ops cleanly if credentials are unset.)
    threading.Thread(target=_safe_initial_sync, daemon=True).start()
    log.info("application started")
    try:
        yield
    finally:
        shutdown_scheduler()
        log.info("application stopped")


def _safe_initial_sync() -> None:
    try:
        run_sync()
    except Exception:  # noqa: BLE001
        log.exception("initial sync failed")


def _ensure_local_placeholder() -> None:
    """Copy the bundled placeholder into /data on first run so operators can swap it."""
    from .paths import BUNDLED_PLACEHOLDER, DATA_DIR

    target = DATA_DIR / "placeholder.svg"
    if not target.exists() and BUNDLED_PLACEHOLDER.exists():
        try:
            atomic_write_bytes(target, BUNDLED_PLACEHOLDER.read_bytes())
        except OSError as exc:
            log.warning("could not seed /data/placeholder.svg: %s", exc)


app = FastAPI(title="TV Tap List", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---- display -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def display_page(request: Request):
    # Cache-bust the TV's CSS/JS by mtime - the display is the hardest surface to
    # hard-refresh, so it must pick up a rebuild on the next normal load.
    return templates.TemplateResponse(
        "display.html",
        {"request": request, "asset_v": _asset_version("css/display.css", "js/display.js")},
    )


@app.get("/api/board")
async def api_board():
    # No-store so proxies never serve a stale board to a TV.
    return JSONResponse(build_board(), headers={"Cache-Control": "no-store"})


def _optional_number(value: str) -> float | None:
    """Parse an optional numeric query value; blank / non-numeric -> None."""
    v = (value or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


@app.get("/api/preview-color")
async def api_preview_color(ebc: str = "", sat: str = "", hex: str = ""):
    """Compute a beer's swatch colour for the admin's live override preview.

    This is delegation, not a second implementation: `colors.resolve_color` owns
    the precedence, so the preview cannot drift from the board (a test pins the
    two against each other rather than a comment claiming they agree).

    What genuinely belongs here is the display unit: ``ebc`` arrives in the
    admin's unit, and `colors.display_color_to_ebc` is the one conversion the
    override save uses too, so the preview cannot show a colour the save would
    then store differently. ``sat`` is a percentage handled by parse_saturation.

    Unknown draws the swatch's grey, because the fallback belongs to the surface
    and the surface this endpoint feeds is a swatch (ADR-0004).
    """
    ebc_val = display_color_to_ebc(_optional_number(ebc),
                                   load_config().get("color_unit", "ebc"))
    color = resolve_color(ebc_val, parse_saturation(sat), hex)
    if color is None:
        return {"color_hex": UNKNOWN_SWATCH_HEX,
                "text_color": text_color_for(UNKNOWN_SWATCH_HEX)}
    return {"color_hex": color.color_hex, "text_color": color.text_color}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": iso_now()}


# ---- image serving (path-sanitised, local only) --------------------------

def _safe_tap_image(filename: str) -> Path | None:
    """Resolve a tap image filename inside TAPS_DIR, rejecting traversal."""
    name = Path(filename).name  # strip any directory components
    candidate = (TAPS_DIR / name).resolve()
    try:
        candidate.relative_to(TAPS_DIR.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def _safe_upcoming_image(filename: str) -> Path | None:
    """Resolve an Upcoming Beer's image filename inside UPCOMING_DIR, rejecting traversal.

    Mirrors `_safe_tap_image` exactly, but scoped to the Upcoming store's own
    directory (app/upcoming_store.py, ADR-0006) rather than TAPS_DIR - an
    Upcoming Beer's photo is never a Tap's photo, and the two stores can hold
    files with colliding names, so the two lookups must stay separate rather
    than merged into one route that tries both directories.
    """
    name = Path(filename).name  # strip any directory components
    candidate = (UPCOMING_DIR / name).resolve()
    try:
        candidate.relative_to(UPCOMING_DIR.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def _img_headers(max_age: int) -> dict[str, str]:
    """Common headers for the /img routes: caching + SVG script neutralisation.

    Tap images and venue logos may be SVG. Embedded via ``<img>`` an SVG can't run
    script, but opened *directly* it is a document that could execute embedded
    JavaScript in our origin. ``script-src 'none'`` blocks every script vector
    (``<script>``, inline handlers, ``javascript:``) and ``sandbox`` isolates the
    document further; a resource's own CSP is ignored when it is embedded as an
    image, so the display is unaffected. ``nosniff`` stops MIME re-interpretation.
    """
    return {
        "Cache-Control": f"public, max-age={max_age}",
        "Content-Security-Policy": "script-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }


@app.get("/img/placeholder")
async def img_placeholder():
    p = placeholder_path()
    if p is None:
        # Inline 1x1 transparent SVG so the display never shows a broken image.
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
        return Response(svg, media_type="image/svg+xml", headers=_img_headers(300))
    return FileResponse(p, headers=_img_headers(300))


@app.get("/img/beer-glass")
async def img_beer_glass(hex: str | None = None, glass: str | None = None):
    """A beer-glass SVG tinted to a resolved Colour (the no-photo placeholder).

    `hex` is the **already-resolved** colour, without the leading # because that
    would start a URL fragment; omitting it means Unknown and gets the
    renderer's amber. `glass` picks the silhouette.

    Colour is never resolved here: the board resolves once and puts the answer
    in the URL, so this route cannot disagree with the swatch. EBC and
    saturation parameters are gone for that reason - an old cached URL still
    carrying them simply renders the Unknown amber until it expires.
    """
    return Response(
        beer_glass_svg(hex, glass),
        media_type="image/svg+xml",
        headers=_img_headers(300),
    )


@app.get("/img/venue-logo")
async def img_venue_logo():
    """Serve the uploaded venue logo from /data (404 if none)."""
    p = venue_logo_path()
    if p is None:
        raise HTTPException(status_code=404, detail="no venue logo")
    return FileResponse(p, headers=_img_headers(60))


@app.get("/img/{filename}")
async def img_file(filename: str):
    p = _safe_tap_image(filename)
    if p is None:
        # Fall back to placeholder rather than 404 so the TV never shows a
        # broken-image icon if a file was archived mid-cycle.
        return await img_placeholder()
    return FileResponse(p, headers=_img_headers(60))


@app.get("/img/upcoming/{filename}")
async def img_upcoming_file(filename: str):
    """Serve an Upcoming Beer's cached photo from /data/upcoming.

    A sibling of `/img/{filename}`, not a fallthrough of it: an Upcoming
    Beer's photo lives in its own store (app/upcoming_store.py, ADR-0006), so
    the teaser's `image_url` (board.py's `_image_url_for`) is built with the
    `/img/upcoming` prefix and always lands here rather than at the Tap
    route, which would either 404 or - worse - serve a same-named Tap photo.
    """
    p = _safe_upcoming_image(filename)
    if p is None:
        return await img_placeholder()
    return FileResponse(p, headers=_img_headers(60))


# ---- admin: auth ---------------------------------------------------------

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    if auth.has_valid_session(request):
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error}
    )


@app.post("/admin/login")
async def login_submit(request: Request, response: Response, password: str = Form("")):
    ip = auth.client_ip(request)
    if auth.is_locked_out(ip):
        log.warning("login locked out for %s", ip)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Too many attempts. Try again shortly."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if auth.verify_password(password):
        auth.record_success(ip)
        redirect = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
        auth.issue_session(redirect, request)
        log.info("admin login success from %s", ip)
        return redirect

    auth.record_failure(ip)
    log.warning("admin login failure from %s", ip)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Incorrect password."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.post("/admin/logout")
async def logout():
    redirect = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    auth.clear_session(redirect)
    return redirect


# ---- admin: dashboard ----------------------------------------------------

def _asset_version(*rels: str) -> str:
    """Cache-busting token = newest mtime among the given static assets.

    Browsers disk-cache CSS/JS aggressively, so a rebuilt image (or an edited file
    in dev) otherwise needs a manual hard-refresh to take effect - annoying for the
    admin, and worse for a wall-mounted TV that is painful to hard-refresh. Keying
    each asset URL to its mtime makes the next normal load pick the new file up.
    """
    latest = 0.0
    for rel in rels:
        try:
            latest = max(latest, (STATIC_DIR / rel).stat().st_mtime)
        except OSError:
            pass
    return str(int(latest))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not auth.has_valid_session(request):
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    cfg = load_config()
    rows = _build_admin_tap_rows(cfg)
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "cfg": cfg,
            # Status is a separate file and a separate template variable, so a
            # template can never reach a sync timestamp through `cfg`.
            "status": load_status(),
            "rows": rows,
            "asset_v": _asset_version("css/admin.css", "js/admin.js"),
            "bf": brewfather_credentials(),
            # Whether the Snapshot tab may offer to carry the Brewfather
            # credentials. The option is hidden rather than disabled when the
            # key is environment-supplied or unset, because a checkbox that
            # silently does nothing is worse than no checkbox.
            "snapshot_credential_option": snapshot.credential_choice_available(),
            "color_label": "SRM" if cfg.get("color_unit") == "srm" else "EBC",
            "venue_logo_url": "/img/venue-logo" if venue_logo_path() else None,
            # Every numeric Settings input takes its min/max from here, so the
            # browser refuses at the point of typing exactly what the store
            # would otherwise clamp silently after the save. One table, one set
            # of numbers - no bound is hand-copied into the template.
            "bounds": {name: {"min": lo, "max": hi}
                       for name, (lo, hi) in SETTINGS_BOUNDS.items()},
            # The teaser-ribbon label's cap and preset list (issue #39). Not a
            # SETTINGS_BOUNDS entry - that table is numeric bounds only - so
            # the template gets its own two values, both from config_store's
            # single declaration.
            "upcoming_label_max": MAX_UPCOMING_LABEL_LEN,
            "upcoming_label_presets": UPCOMING_LABEL_PRESETS,
            # Theme + glassware pickers.
            "themes": THEMES,
            "theme_fields": THEME_FIELD_LABELS,
            "theme_custom": cfg.get("theme_custom") or DEFAULT_THEME,
            "glass_types": GLASS_TYPES,
            # Banner when the admin is open with no login (demo mode, no password).
            "demo_open": auth.demo_admin_open(),
            # Data-durability banner, decided once at startup: "not_mapped",
            # "data_replaced", or None for the healthy case.
            "persistence_warning": persistence.admin_banner(),
            # Update check status for the admin status panel.
            "update_current_version": current_version(),
        },
    )


def _color_in_unit(ebc, unit: str):
    """Convert a stored EBC value to the admin's display unit for prefilling."""
    if ebc is None or ebc == "":
        return ""
    val = ebc_to_srm(ebc) if unit == "srm" else float(ebc)
    return int(val) if float(val).is_integer() else round(val, 1)


def _saturation_percent(value):
    """Stored 0..1 saturation -> a percentage for the admin form (blank if unset)."""
    sat = parse_saturation(value)
    return "" if sat is None else int(round(sat * 100))


def _tri_to_form(value) -> str:
    """A stored tri-state (True/False/None) -> a select value ("true"/"false"/"")."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def _tri_from_form(value: str) -> bool | None:
    """A select value ("true"/"false"/"") -> a stored tri-state (True/False/None)."""
    v = (value or "").strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    return None


def _shadow_beer_name(tap: int) -> str | None:
    """The Brewfather Beer waiting under a Manual override on this Slot.

    None when there is no Brewfather file for the Slot. A file that exists but
    will not read still counts as waiting - the operator should be told the Slot
    will not go Vacant - so it falls back to a generic label rather than
    pretending nothing is there.
    """
    if not taps.exists(tap, taps.Source.BREWFATHER):
        return None
    shadow = taps.read(tap, taps.Source.BREWFATHER)
    name = shadow.beer.name if shadow else ""
    return name or "a Brewfather beer"


def _build_admin_tap_rows(cfg: dict) -> list[dict]:
    """Per-tap admin state: override on/off and current values to prefill.

    Resolution goes through the Tap file store, so a row shows exactly what the
    display shows for that Slot - same Source, same photo. It used to resolve
    the photo across *both* Sources (the Manual image, else the Brewfather one)
    while the display resolved within one, so a Manual Tap with no photo showed
    the Brewfather photo here and a glass placeholder on the TV. A Tap comes
    entirely from one Source; a part-wise fallback is not representable in a
    TapFile, and Admin is only useful as a preview if it agrees with the TV.
    """
    rows: list[dict] = []
    num_taps = int(cfg.get("num_taps", 0) or 0)
    unit = cfg.get("color_unit", "ebc")
    for tap in range(1, num_taps + 1):
        tap_file = taps.resolve(tap)
        # "Is this Slot Manual?" has one answer now: the winning Source.
        override = tap_file is not None and tap_file.source is taps.Source.MANUAL
        # A Vacant Slot prefills from an empty Beer rather than from an empty
        # dict, so the form's fields are the type's fields either way.
        beer = tap_file.beer if tap_file is not None else Beer()
        presentation = (tap_file.presentation if tap_file is not None
                        else TapPresentation())
        img = tap_file.image if tap_file is not None else None
        rows.append({
            "tap": tap,
            "override": override,
            "name": beer.name,
            "abv": beer.abv if beer.abv is not None else "",
            "ibu": beer.ibu if beer.ibu is not None else "",
            "og": beer.og if beer.og is not None else "",
            "fg": beer.fg if beer.fg is not None else "",
            # Colour prefilled in the admin's chosen unit (stored as EBC).
            "color_value": _color_in_unit(beer.ebc, unit),
            "saturation": _saturation_percent(beer.saturation),
            "color_override": beer.color_override or "",
            "glass": beer.glass or "",
            "show_og": _tri_to_form(presentation.show_og),
            "show_fg": _tri_to_form(presentation.show_fg),
            # The description is the markdown body, a named field on the
            # TapFile rather than a synthesised front-matter key.
            "description": (tap_file.body if tap_file is not None else "") or "",
            # The filename decides the Source, exactly as it does on the board;
            # the front-matter `source:` key is written for a human reading the
            # file and is never read back as truth.
            "source": str(tap_file.source) if tap_file is not None else None,
            "image_url": f"/img/{img.name}" if img else None,
            # The shadow hint: which Brewfather Beer is waiting under this
            # override. Both Sources holding a file for one Slot is the normal
            # case now (the Brewfather Tap is kept warm underneath), so the row
            # says so in words rather than letting the operator discover a
            # second file in the data directory and delete it. Deliberately a
            # name and nothing else - no thumbnail, because a second photo is
            # exactly the Admin-versus-TV divergence just closed, reintroduced
            # as a picture.
            "shadow_name": _shadow_beer_name(tap) if override else None,
        })
    return rows


# ---- admin: settings -----------------------------------------------------

class SettingsForm(BaseModel):
    """The Settings the Admin form submits, declared exactly once.

    This replaces a signature that listed every field as a typed `Form()`
    parameter and a body that then named all of them again to build the update
    dict - two lists that had to be kept in step by hand. The model is now the
    single list, and `test_api.py` asserts it matches the Settings schema, so a
    field added to one side and not the other fails the suite instead of quietly
    never saving.

    The types are load-bearing, not decoration. The Admin client posts every
    checkbox explicitly as the string ``"true"`` or ``"false"`` (an unchecked box
    is otherwise simply absent, which would leave a toggle stuck on), and
    ``bool("false")`` is **True** in Python - so handing the raw form strings to
    the store would save every unchecked box as checked. Pydantic parses the
    string boolean properly, and non-numeric input still raises FastAPI's own
    422 exactly as the typed parameters did.

    Defaults are the *form's* defaults, deliberately not the schema's: a missing
    checkbox means off, whatever `DEFAULT_CONFIG` says the field starts as. The
    three fields with no default are required, as they were before.

    `theme_custom` is absent because the palette arrives as separate
    ``theme_<key>`` fields, which the route unprefixes below.
    """

    # Extra form fields (the theme_<key> colours, the CSRF-free odds and ends a
    # browser sends) are ignored rather than rejected: this model describes the
    # Settings in the post, not the whole post.
    model_config = ConfigDict(extra="ignore")

    brewfather_user_id: str = ""
    brewfather_api_key: str = ""
    include_conditioning: bool = False
    include_fermenting: bool = False
    show_upcoming_previews: bool = False
    max_upcoming_previews: int = 3
    upcoming_label: str = ""
    show_upcoming_status: bool = False
    show_upcoming_subtitle: bool = False
    show_upcoming_abv: bool = False
    num_taps: int
    hide_vacant_taps: bool = False
    announcement_text: str = ""
    max_archive_age_days: int
    max_archive_storage_mb: int
    color_unit: str = "ebc"
    show_abv: bool = False
    show_ibu: bool = False
    show_color: bool = False
    show_og: bool = False
    show_fg: bool = False
    hide_abv_when_empty: bool = False
    hide_ibu_when_empty: bool = False
    hide_color_when_empty: bool = False
    hide_og_when_empty: bool = False
    hide_fg_when_empty: bool = False
    show_source_badge: bool = False
    theme: str = "default"
    glass_type: str = DEFAULT_GLASS
    tap_photo_preset: str = "default"
    tap_text_preset: str = "default"
    tap_image_scale: float = 1.0
    tap_text_scale: float = 1.0
    paginate: bool = False
    page_size: int = 6
    rotation_seconds: int = 30
    venue_logo_height_vh: int = 0


# Settings that exist in the schema but are deliberately not on the Settings
# form, each for its own reason. Named so the field-set guard below can be exact
# about the difference instead of tolerating any mismatch.
SETTINGS_NOT_ON_THE_FORM = frozenset({
    # Arrives as separate theme_<key> colour fields; assembled by the route.
    "theme_custom",
    # Written by the venue-logo upload route, not typed by anyone.
    "venue_logo",
    # No control on the Settings tab: an air-gapped deploy turns the update
    # check off by editing config.json. It must stay off this model as well as
    # off the form - a field here that the form never posts would take the
    # model's default on every Save and quietly switch the check back on.
    "update_check_enabled",
})


@app.post("/admin/settings")
async def save_settings(
    request: Request,
    settings: Annotated[SettingsForm, Form()],
    _: None = Depends(auth.require_admin),
):
    """Persist the Settings form. Parse, hand to the store, answer.

    No range check lives here on purpose. The route used to refuse a negative
    tap count and negative cleanup limits with a 422 while the store silently
    clamped the same values, and the ceiling on the tap count was enforced in
    the store alone - so an operator could save 5000 taps, get a success, and
    watch the field snap to the bound on reload with no explanation. Clamping in
    `config_store` is now the single enforcement point and the form's inputs
    carry the same bounds, so the browser stops the value being typed. Do not
    restore the checks; see CONTEXT.md's Known hazards for why rejection here
    would gain nothing the clamp does not already guarantee.
    """
    # The custom palette arrives as theme_<key> fields, which are not Settings
    # names. Unprefix them and let the store validate each colour - a blank or
    # malformed one falls back to the default palette in `coerce_custom_theme`.
    form = await request.form()
    theme_custom = {key: form.get(f"theme_{key}") for key in THEME_KEYS}

    saved = apply_settings(**settings.model_dump(), theme_custom=theme_custom)
    # Log what was *saved*, not what was posted: the two differ whenever a bound
    # clamped, and the log is the only trace of that an operator can consult.
    log.info("admin saved settings (num_taps=%d color_unit=%s)",
             saved["num_taps"], saved["color_unit"])
    return {"ok": True}


# ---- admin: uploads ------------------------------------------------------

# Cap uploaded images / logos. Admin-only, but bound the in-memory read so a
# stray huge file can't spike memory; well above any real logo or beer photo.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def _read_upload_capped(upload: UploadFile) -> bytes:
    """Read an upload fully into memory, rejecting anything over the cap (413).

    Reads at most cap+1 bytes, so an oversized file is refused without slurping
    the whole thing. Callers MUST invoke this (and validate the extension) before
    any filesystem side effect, so a rejected upload never deletes existing data.
    """
    data = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    return data


# ---- admin: venue logo ---------------------------------------------------

@app.post("/admin/venue-logo")
async def venue_logo(
    request: Request,
    _: None = Depends(auth.require_admin),
    remove: bool = Form(False),
    image: UploadFile | None = None,
):
    """Upload or remove the venue logo (stored under /data as venue_logo.<ext>)."""
    with JOB_LOCK:
        if remove or image is None or not image.filename:
            for ext in VENUE_LOGO_EXTS:
                safe_unlink(DATA_DIR / f"venue_logo{ext}")
            update_config(venue_logo=None)
            log.info("venue logo removed")
            return {"ok": True, "venue_logo_url": None}

        # Validate the extension and read/size-check the bytes BEFORE removing the
        # current logo, so a rejected upload never leaves the venue with no logo.
        ext = Path(image.filename).suffix.lower()
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in VENUE_LOGO_EXTS:
            raise HTTPException(status_code=422, detail=f"Unsupported image type: {ext}")
        data = _read_upload_capped(image)
        # Clear any existing logo (possibly a different extension), then write.
        for old_ext in VENUE_LOGO_EXTS:
            safe_unlink(DATA_DIR / f"venue_logo{old_ext}")
        dest = DATA_DIR / f"venue_logo{ext}"
        atomic_write_bytes(dest, data)
        update_config(venue_logo=dest.name)
        log.info("venue logo uploaded (%s)", dest.name)
        return {"ok": True, "venue_logo_url": "/img/venue-logo"}


# ---- admin: manual overrides ---------------------------------------------

def _validated_upload(upload: UploadFile | None) -> tuple[bytes, str] | None:
    """Vet an uploaded beer photo and return its bytes plus extension.

    Both HTTP concerns - the extension allow-list and the size cap - are settled
    here, and nothing on disk has changed by the time this returns, so a
    rejected upload never deletes the beer's existing image. The domain
    operation is then handed bytes the route vouches for; the store owns the
    filename and the sweep that removes a previously stored image with a
    different extension.
    """
    if upload is None or not upload.filename:
        return None
    ext = Path(upload.filename).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in taps.IMAGE_EXTS:
        raise HTTPException(status_code=422, detail=f"Unsupported image type: {ext}")
    return _read_upload_capped(upload), ext


@app.post("/admin/override/{tap}")
async def save_override(
    tap: int,
    _: None = Depends(auth.require_admin),
    enabled: bool = Form(False),
    name: str = Form(""),
    abv: str = Form(""),
    ibu: str = Form(""),
    og: str = Form(""),
    fg: str = Form(""),
    color: str = Form(""),     # colour in the admin's display unit (EBC or SRM)
    saturation: str = Form(""),  # optional colour-saturation override, as a %
    color_override: str = Form(""),  # exact #rrggbb override (wins over EBC colour)
    glass: str = Form(""),     # glassware key, or blank to inherit the global default
    show_og: str = Form(""),   # per-tap tri-state: "", "true", "false"
    show_fg: str = Form(""),
    description: str = Form(""),
    image: UploadFile | None = None,
):
    """Save or clear a Slot's Manual override; `admin_ops` decides what that means.

    The route's whole job is HTTP: vet the upload, translate the form's
    tri-state selects, and turn a domain rejection into a 422. Which files move,
    what the front matter says, and the Brewfather Tap left warm underneath all
    live in `admin_ops` so they can be asserted without a request.
    """
    if tap < 1:
        raise HTTPException(status_code=422, detail="Invalid tap number")

    if not enabled:
        admin_ops.clear_override(tap)
        return {"ok": True, "override": False}

    # Vetted before the domain call so a bad extension or an oversized file is
    # refused with nothing written; the domain operation then rejects any bad
    # value before it writes either, so neither can orphan the other.
    upload = _validated_upload(image)
    try:
        admin_ops.save_override(
            tap,
            name=name, abv=abv, ibu=ibu, og=og, fg=fg,
            color=color, saturation=saturation, color_override=color_override,
            glass=glass,
            show_og=_tri_from_form(show_og), show_fg=_tri_from_form(show_fg),
            description=description, image=upload,
            unit=load_config().get("color_unit", "ebc"),
        )
    except admin_ops.OverrideRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # The photo is asked of the store rather than read off the front matter the
    # save returned: the store finds it by globbing the Slot's stem, so this
    # cannot report an image the file does not actually have beside it.
    stored_image = taps.image_for(tap, taps.Source.MANUAL)
    return {"ok": True, "override": True,
            "image_url": f"/img/{stored_image.name}" if stored_image else None}


# ---- admin: manual sync trigger ------------------------------------------

@app.post("/admin/sync")
async def trigger_sync(_: None = Depends(auth.require_admin)):
    # Run synchronously so the admin sees the result; sync takes JOB_LOCK itself.
    result = run_sync()
    return result


# ---- admin: Snapshot export / import -------------------------------------
#
# Three routes because the import is two steps, and it is two steps for one
# reason: the Brewfather question can only be answered once the *Snapshot's*
# credential is known, and that is not knowable until the file is on the box.
# The alternative - refuse the first attempt and make the operator upload again
# with an answer attached - means sending a Snapshot that can run to gigabytes
# twice over a venue LAN. Staging costs one file in the data directory instead.
#
# Note what the browser is never asked to do: it does not decide whether to
# prompt. `/admin/snapshot/stage` hands back the case the server resolved, and
# the admin renders the matching copy. The rule that picks the case lives in
# `snapshot.plan_import` and only there.

@app.get("/admin/snapshot")
async def export_snapshot(
    _: None = Depends(auth.require_admin),
    credentials: bool = False,
):
    """Stream a Snapshot of the data directory as a zip download.

    A GET because it is a read: nothing on the box changes, and a plain
    navigation gives the operator the browser's own download UI, including
    progress on a Snapshot that takes minutes.

    `credentials` is the export's opt-in checkbox and defaults to off. The
    module re-checks that the key is genuinely in `config.json` before honouring
    it, so this route carries no rule of its own.
    """
    body = snapshot.settings_bytes(credentials)
    entries = snapshot.enumerate_entries()
    return StreamingResponse(
        snapshot.stream_snapshot(body, entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{snapshot.snapshot_filename()}"',
            # A Snapshot is a point in time and must never be served from a
            # cache; it also may carry the Brewfather key.
            "Cache-Control": "no-store",
        },
    )


@app.post("/admin/snapshot/stage")
async def stage_snapshot(request: Request, _: None = Depends(auth.require_admin)):
    """Receive a Snapshot, validate it whole, and report what it will take to import.

    The body is the zip itself rather than a multipart upload, which is what
    lets it stream straight to disk in the data directory. The multipart path
    would spool it into the system temp directory - Starlette's parser hands
    every file part a `SpooledTemporaryFile` that rolls over at 1 MB, and
    neither the size nor the directory is settable per request - and the
    admin's 10 MB `MAX_UPLOAD_BYTES` cap, which exists to bound an in-memory
    read of a beer photo, has no business anywhere near a Snapshot.

    Nothing is written into the data directory proper here: on any failure the
    staged file is deleted and the box is exactly as it was.
    """
    ensure_dirs()
    snapshot.discard_staged()
    try:
        with snapshot.STAGED_UPLOAD_PATH.open("wb") as staged:
            async for chunk in request.stream():
                staged.write(chunk)
        with snapshot.open_snapshot(snapshot.STAGED_UPLOAD_PATH) as zf:
            plan = snapshot.plan_import(snapshot.read_snapshot_settings(zf))
    except snapshot.SnapshotRejected as exc:
        snapshot.discard_staged()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BaseException:
        snapshot.discard_staged()
        raise
    log.info("snapshot staged for import (decision=%s)", plan.kind)
    return {
        "ok": True,
        "decision": plan.kind,
        "box_has_key": plan.box_has_key,
        "key_from_env": plan.key_from_env,
        "snapshot_has_key": plan.snapshot_has_key,
    }


@app.post("/admin/snapshot/discard")
async def discard_snapshot(_: None = Depends(auth.require_admin)):
    """Throw away a staged Snapshot the operator decided not to import.

    Without this, cancelling at the Brewfather question would leave a file the
    size of the whole data directory sitting there until the next upload
    replaced it.
    """
    snapshot.discard_staged()
    return {"ok": True}


@app.post("/admin/snapshot/import")
async def import_snapshot(
    _: None = Depends(auth.require_admin),
    keep_syncing: str = Form(""),
):
    """Restore the staged Snapshot. `keep_syncing` is "true"/"false"/"" (not asked).

    The tri-state is the same shape the override form's selects use: blank means
    the operator was never asked, which is legitimate for the two cases that
    carry no question and a 409 for the one that does.
    """
    if not snapshot.STAGED_UPLOAD_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Snapshot is waiting to be imported. Choose a file and upload it again.",
        )
    try:
        result = snapshot.import_snapshot(
            snapshot.STAGED_UPLOAD_PATH,
            keep_syncing=_tri_from_form(keep_syncing),
        )
    except snapshot.SnapshotRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except snapshot.DecisionRequired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    finally:
        # The staged file goes on every path, including a partial restore: it is
        # a copy of data that now lives in the data directory proper, and
        # leaving it would double the box's disk use until the next import.
        snapshot.discard_staged()
    return {"ok": True, **result}


# ---- admin: update check --------------------------------------------------

@app.get("/api/update-status")
async def api_update_status():
    """Return the current update-check state.

    What the last check found is Status (status.json); whether checking is
    enabled at all is a Setting (config.json). Public (no auth) so the admin
    page can poll it, which is safe because it carries no secrets - just version
    strings and a URL. Note this is the ONLY endpoint that exposes any Status,
    and it deliberately exposes none of the sync fields: /api/board omits Status
    entirely, `last_sync_error` included, because that can carry upstream API
    error text.
    """
    cfg = load_config()
    status = load_status()
    cur = current_version()
    latest = status.get("update_latest_version")
    enabled = bool(cfg.get("update_check_enabled", True))
    return {
        "current_version": cur,
        "latest_version": latest,
        "latest_url": status.get("update_latest_url"),
        "update_available": is_update_available(latest, cur),
        # `status` is the honest four-state answer; `update_available` is kept
        # for compatibility and stays the "definitely behind" signal only. An
        # untagged build reports status="unknown" with update_available=false,
        # and the admin must not read that pair as "up to date" (issue #26).
        "status": update_state(latest, cur, enabled),
        "last_check": status.get("update_last_check"),
        "enabled": enabled,
    }


@app.post("/admin/check-update")
async def trigger_update_check(_: None = Depends(auth.require_admin)):
    """Run an update check immediately (admin button)."""
    result = check_for_updates()
    return result
