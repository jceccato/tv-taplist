"""FastAPI application: TV display, admin interface, board API, asset serving.

Routes
------
Public display:
  GET  /                -> TV display page (fully self-contained, local assets)
  GET  /api/board       -> fully-resolved board JSON (frontend never parses md)
  GET  /img/{filename}  -> tap image from /data/taps (path-sanitised)
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
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, markdown_store as md
from .archive import archive_tap
from .atomic import JOB_LOCK, atomic_write_bytes, safe_unlink
from .beer_glass import beer_glass_svg
from .board import build_board
from .brewfather import run_sync
from .colors import ebc_to_srm, srm_to_ebc
from .config_store import (
    MAX_VENUE_LOGO_VH,
    brewfather_credentials,
    load_config,
    save_config,
)
from .demo import maybe_seed_demo
from .paths import (
    DATA_DIR,
    STATIC_DIR,
    TAPS_DIR,
    TEMPLATES_DIR,
    VENUE_LOGO_EXTS,
    ensure_dirs,
    placeholder_path,
    venue_logo_path,
)
from .scheduler import shutdown_scheduler, start_scheduler
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
    load_config()  # first-run bootstrap of config.json
    _ensure_local_placeholder()
    maybe_seed_demo()
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
    return templates.TemplateResponse("display.html", {"request": request})


@app.get("/api/board")
async def api_board():
    # No-store so proxies never serve a stale board to a TV.
    return JSONResponse(build_board(), headers={"Cache-Control": "no-store"})


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


@app.get("/img/placeholder")
async def img_placeholder():
    p = placeholder_path()
    if p is None:
        # Inline 1x1 transparent SVG so the display never shows a broken image.
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
        return Response(svg, media_type="image/svg+xml")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=300"})


@app.get("/img/beer-glass")
async def img_beer_glass(ebc: float | None = None):
    """A beer-glass SVG tinted to the beer's colour (the no-photo placeholder)."""
    return Response(
        beer_glass_svg(ebc),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/img/venue-logo")
async def img_venue_logo():
    """Serve the uploaded venue logo from /data (404 if none)."""
    p = venue_logo_path()
    if p is None:
        raise HTTPException(status_code=404, detail="no venue logo")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=60"})


@app.get("/img/{filename}")
async def img_file(filename: str):
    p = _safe_tap_image(filename)
    if p is None:
        # Fall back to placeholder rather than 404 so the TV never shows a
        # broken-image icon if a file was archived mid-cycle.
        return await img_placeholder()
    return FileResponse(p, headers={"Cache-Control": "public, max-age=60"})


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
            "rows": rows,
            "bf": brewfather_credentials(),
            "color_label": "SRM" if cfg.get("color_unit") == "srm" else "EBC",
            "venue_logo_url": "/img/venue-logo" if venue_logo_path() else None,
            "max_logo_vh": MAX_VENUE_LOGO_VH,
        },
    )


def _color_in_unit(ebc, unit: str):
    """Convert a stored EBC value to the admin's display unit for prefilling."""
    if ebc is None or ebc == "":
        return ""
    val = ebc_to_srm(ebc) if unit == "srm" else float(ebc)
    return int(val) if float(val).is_integer() else round(val, 1)


def _build_admin_tap_rows(cfg: dict) -> list[dict]:
    """Per-tap admin state: override on/off and current values to prefill."""
    rows: list[dict] = []
    num_taps = int(cfg.get("num_taps", 0) or 0)
    unit = cfg.get("color_unit", "ebc")
    for tap in range(1, num_taps + 1):
        override = md.is_manual_override(tap)
        data = md.read_tap_file(md.custom_md_path(tap)) if override else md.read_tap_file(md.bf_md_path(tap))
        data = data or {}
        img = md.find_image_for(f"custom_tap_{tap}") or md.find_image_for(f"bf_tap_{tap}")
        rows.append({
            "tap": tap,
            "override": override,
            "name": data.get("name") or "",
            "abv": data.get("abv") if data.get("abv") is not None else "",
            "ibu": data.get("ibu") if data.get("ibu") is not None else "",
            # Colour prefilled in the admin's chosen unit (stored as EBC).
            "color_value": _color_in_unit(data.get("ebc"), unit),
            "description": data.get("description") or "",
            "source": data.get("source") or ("custom" if override else None),
            "image_url": f"/img/{img.name}" if img else None,
        })
    return rows


# ---- admin: settings -----------------------------------------------------

@app.post("/admin/settings")
async def save_settings(
    request: Request,
    _: None = Depends(auth.require_admin),
    brewfather_user_id: str = Form(""),
    brewfather_api_key: str = Form(""),
    num_taps: int = Form(...),
    hide_vacant_taps: bool = Form(False),
    announcement_text: str = Form(""),
    max_archive_age_days: int = Form(...),
    max_archive_storage_mb: int = Form(...),
    color_unit: str = Form("ebc"),
    show_abv: bool = Form(False),
    show_ibu: bool = Form(False),
    show_color: bool = Form(False),
    hide_abv_when_empty: bool = Form(False),
    hide_ibu_when_empty: bool = Form(False),
    hide_color_when_empty: bool = Form(False),
    venue_logo_height_vh: int = Form(0),
):
    if num_taps < 0:
        raise HTTPException(status_code=422, detail="Number of taps must be >= 0")
    if max_archive_age_days < 0 or max_archive_storage_mb < 0:
        raise HTTPException(status_code=422, detail="Cleanup limits must be >= 0")

    cfg = load_config()
    updates = {
        "num_taps": num_taps,
        "hide_vacant_taps": hide_vacant_taps,
        "announcement_text": announcement_text,
        "max_archive_age_days": max_archive_age_days,
        "max_archive_storage_mb": max_archive_storage_mb,
        "color_unit": "srm" if color_unit.lower() == "srm" else "ebc",
        "show_abv": show_abv,
        "show_ibu": show_ibu,
        "show_color": show_color,
        "hide_abv_when_empty": hide_abv_when_empty,
        "hide_ibu_when_empty": hide_ibu_when_empty,
        "hide_color_when_empty": hide_color_when_empty,
        "venue_logo_height_vh": max(0, min(MAX_VENUE_LOGO_VH, venue_logo_height_vh)),
    }
    # Only persist Brewfather credentials that are NOT managed via env vars, so
    # an env-set key is never written to config.json.
    creds = brewfather_credentials()
    if not creds["user_from_env"]:
        updates["brewfather_user_id"] = brewfather_user_id.strip()
    if not creds["key_from_env"]:
        updates["brewfather_api_key"] = brewfather_api_key.strip()

    cfg.update(updates)
    save_config(cfg)
    log.info("admin saved settings (num_taps=%d color_unit=%s)", num_taps, updates["color_unit"])
    return {"ok": True}


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
        # Always clear any existing logo first so we never keep two extensions.
        for ext in VENUE_LOGO_EXTS:
            safe_unlink(DATA_DIR / f"venue_logo{ext}")

        if remove or image is None or not image.filename:
            save_config({**load_config(), "venue_logo": None})
            log.info("venue logo removed")
            return {"ok": True, "venue_logo_url": None}

        ext = Path(image.filename).suffix.lower()
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in VENUE_LOGO_EXTS:
            raise HTTPException(status_code=422, detail=f"Unsupported image type: {ext}")
        dest = DATA_DIR / f"venue_logo{ext}"
        atomic_write_bytes(dest, image.file.read())
        save_config({**load_config(), "venue_logo": dest.name})
        log.info("venue logo uploaded (%s)", dest.name)
        return {"ok": True, "venue_logo_url": "/img/venue-logo"}


# ---- admin: manual overrides ---------------------------------------------

def _save_uploaded_image(upload: UploadFile, tap: int) -> str | None:
    """Save an uploaded custom image as custom_tap_X.<ext>, return filename."""
    if upload is None or not upload.filename:
        return None
    ext = Path(upload.filename).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in md.IMAGE_EXTS:
        raise HTTPException(status_code=422, detail=f"Unsupported image type: {ext}")
    stem = f"custom_tap_{tap}"
    # Remove any prior custom image with a different extension.
    for old in md.IMAGE_EXTS:
        old_path = TAPS_DIR / f"{stem}{old}"
        if old_path.exists() and old != ext:
            safe_unlink(old_path)
    data = upload.file.read()
    dest = TAPS_DIR / f"{stem}{ext}"
    atomic_write_bytes(dest, data)
    return dest.name


@app.post("/admin/override/{tap}")
async def save_override(
    tap: int,
    request: Request,
    _: None = Depends(auth.require_admin),
    enabled: bool = Form(False),
    name: str = Form(""),
    abv: str = Form(""),
    ibu: str = Form(""),
    color: str = Form(""),     # colour in the admin's display unit (EBC or SRM)
    description: str = Form(""),
    image: UploadFile | None = None,
):
    if tap < 1:
        raise HTTPException(status_code=422, detail="Invalid tap number")

    def _num(v: str):
        v = (v or "").strip()
        if v == "":
            return None
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except ValueError:
            raise HTTPException(status_code=422, detail=f"'{v}' is not a number")

    unit = load_config().get("color_unit", "ebc")

    def _color_to_ebc(v: str):
        """Parse the colour input (in the chosen unit) and store it as EBC."""
        num = _num(v)
        if num is None:
            return None
        ebc = srm_to_ebc(num) if unit == "srm" else float(num)
        return int(ebc) if float(ebc).is_integer() else round(ebc, 1)

    with JOB_LOCK:
        if not enabled:
            # Release the slot back to Brewfather control: archive custom files.
            archived = archive_tap(f"custom_tap_{tap}")
            log.info("override cleared for tap %d (archived=%s)", tap, archived)
            return {"ok": True, "override": False}

        # Enabling/saving an override: write custom_tap_X.md (+ optional image),
        # and archive any Brewfather data for this slot.
        image_name = _save_uploaded_image(image, tap) if image is not None else None
        if image_name is None:
            existing = md.find_image_for(f"custom_tap_{tap}")
            image_name = existing.name if existing else None

        front_matter = {
            "name": name.strip() or f"Tap {tap}",
            "abv": _num(abv),
            "ibu": _num(ibu),
            "ebc": _color_to_ebc(color),
            "source": "custom",
            "image": image_name,
            "updated": iso_now(),
        }
        md.write_tap_file(md.custom_md_path(tap), front_matter, description)

        # Archive any bf_tap_X for this slot so it is set aside cleanly.
        archive_tap(f"bf_tap_{tap}")
        log.info("override saved for tap %d (name=%r image=%s)", tap, front_matter["name"], image_name)
        return {"ok": True, "override": True, "image_url": f"/img/{image_name}" if image_name else None}


# ---- admin: manual sync trigger ------------------------------------------

@app.post("/admin/sync")
async def trigger_sync(_: None = Depends(auth.require_admin)):
    # Run synchronously so the admin sees the result; sync takes JOB_LOCK itself.
    result = run_sync()
    return result
