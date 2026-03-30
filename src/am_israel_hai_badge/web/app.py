"""FastAPI application — badge serving, Supabase OAuth, dashboard."""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..api import fetch_github_commit_count, resolve_area_names, _fetch_cities_data
from ..badge import generate_badge
from .auth import (
    _COOKIE_NAME,
    _PKCE_COOKIE,
    create_session_cookie,
    exchange_code,
    extract_user_info,
    get_login_url,
    read_session_cookie,
)
from .cache import AlertCache
from .db import Database
from .worker import start_worker

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_TEMPLATES = Path(__file__).parent / "templates"

# ── Shared state ───────────────────────────────────────────────────────

alert_cache = AlertCache()
db: Database = None  # type: ignore[assignment]  # set in lifespan
_worker_thread = None
_worker_stop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, _worker_thread, _worker_stop
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _DATABASE_URL:
        db = Database(_DATABASE_URL)
    else:
        db = Database(_DATA_DIR / "shelter.db")
    _worker_thread, _worker_stop = start_worker(alert_cache)
    yield
    if _worker_stop:
        _worker_stop.set()
    db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


# ── Helpers ────────────────────────────────────────────────────────────

def _get_current_user(request: Request) -> dict | None:
    """Return user info from session cookie, or None."""
    cookie = request.cookies.get(_COOKIE_NAME, "")
    if not cookie:
        return None
    return read_session_cookie(cookie)


def _render(name: str, **ctx) -> HTMLResponse:
    path = _TEMPLATES / name
    html = path.read_text(encoding="utf-8")
    for key, val in ctx.items():
        html = html.replace(f"{{{{{key}}}}}", str(val))
    return HTMLResponse(html)


def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


# ── Badge endpoint (public) ───────────────────────────────────────────

@app.get("/badge/{token}.svg")
async def serve_badge(token: str):
    badge = db.get_badge_by_token(token)
    if not badge:
        raise HTTPException(404, "Badge not found")

    area_names = json.loads(badge["area_names"])
    s_24h, s_7d, s_30d = alert_cache.get_badge_data(area_names)

    commits = 0
    if badge.get("github_login") and badge.get("github_token"):
        try:
            commits = fetch_github_commit_count(
                badge["github_login"], token=badge["github_token"],
            )
        except Exception:
            pass

    svg = generate_badge(s_24h, s_7d, s_30d, commits)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=900, s-maxage=900",
        },
    )


# ── Auth endpoints (Supabase GitHub OAuth) ────────────────────────────

@app.get("/auth/login")
async def auth_login(request: Request):
    callback = f"{_base_url(request)}/auth/callback"
    auth_url, code_verifier = get_login_url(callback)
    response = RedirectResponse(auth_url)
    response.set_cookie(
        _PKCE_COOKIE, code_verifier,
        max_age=600, httponly=True, samesite="lax",
    )
    return response


@app.get("/auth/callback")
async def auth_callback(code: str, request: Request):
    code_verifier = request.cookies.get(_PKCE_COOKIE, "")
    if not code_verifier:
        raise HTTPException(400, "Missing PKCE verifier")

    session = exchange_code(code, code_verifier)
    user_info = extract_user_info(session)

    cookie_name, cookie_value, max_age = create_session_cookie(
        user_id=user_info["id"],
        github_login=user_info["github_login"],
        avatar_url=user_info["avatar_url"],
        provider_token=user_info.get("provider_token", ""),
    )
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        cookie_name, cookie_value,
        max_age=max_age, httponly=True, samesite="lax",
    )
    response.delete_cookie(_PKCE_COOKIE)
    return response


@app.post("/auth/logout")
async def auth_logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(_COOKIE_NAME)
    return response


# ── Dashboard ─────────────────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard(request: Request):
    user = _get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")

    badges = db.list_badges_for_user(user["uid"])
    base = _base_url(request)

    badge_rows = ""
    for b in badges:
        areas = ", ".join(json.loads(b["area_names"]))
        url = f"{base}/badge/{b['token']}.svg"
        embed = f"![Time in Shelter]({url})"
        badge_rows += f"""
    <div class="badge-card">
      <div class="badge-card-preview">
        <img src="/badge/{b['token']}.svg" alt="badge"/>
      </div>
      <div class="badge-card-meta">
        <div class="badge-card-area">{areas}</div>
        <div class="embed-group">
          <div class="embed-label">Embed Code</div>
          <div class="embed-row">
            <input type="text" value="{embed}" readonly onclick="this.select()" class="embed-code"/>
            <button type="button" class="btn-icon btn-copy" title="Copy">
              <svg class="icon-copy" viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
              <svg class="icon-check" viewBox="0 0 24 24" style="display:none"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
            </button>
            <form method="post" action="/api/badges/{b['token']}/delete"
                  onsubmit="return confirm('Delete this badge?')" style="margin:0">
              <button type="submit" class="btn-icon btn-delete" title="Delete">
                <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>"""

    # Build datalist options for area autocomplete
    area_opts = ""
    for area in _get_area_options():
        labels = [area["he"]]
        for lang in ("en", "ru", "ar"):
            if lang in area:
                labels.append(area[lang])
        display = " / ".join(labels)
        area_opts += f'<option value="{area["he"]}" label="{display}"></option>\n'

    return _render(
        "dashboard.html",
        username=user["login"],
        avatar=user.get("avatar", ""),
        badge_rows=badge_rows or '<div class="empty-state">No badges yet. Create one above.</div>',
        area_options=area_opts,
        record_count=alert_cache.record_count,
        last_refresh=str(alert_cache.last_refresh or "loading..."),
    )


# ── Badge CRUD API ────────────────────────────────────────────────────

@app.post("/api/badges")
async def create_badge(request: Request, areas: str = Form(...)):
    user = _get_current_user(request)
    if not user:
        raise HTTPException(401)

    raw_names = [a.strip() for a in areas.split(",") if a.strip()]
    if not raw_names:
        raise HTTPException(400, "No areas specified")

    resolved = resolve_area_names(raw_names)
    db.create_badge(
        user_id=user["uid"],
        github_login=user["login"],
        area_names=resolved,
        github_token=user.get("gh_token", ""),
    )
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/api/badges/{token}/delete")
async def delete_badge(token: str, request: Request):
    user = _get_current_user(request)
    if not user:
        raise HTTPException(401)
    db.delete_badge(token, user["uid"])
    return RedirectResponse("/dashboard", status_code=303)


# ── Areas API ─────────────────────────────────────────────────────────

def _get_area_options() -> list[dict]:
    """Return area list with all language variants for autocomplete."""
    try:
        cities = _fetch_cities_data()
    except Exception:
        return []
    areas: list[dict] = []
    for he_name, info in cities.items():
        if not isinstance(info, dict):
            continue
        entry = {"he": he_name}
        for lang in ("en", "ru", "ar"):
            val = info.get(lang, "")
            if val:
                entry[lang] = val
        areas.append(entry)
    areas.sort(key=lambda a: a.get("en", a["he"]))
    return areas


@app.get("/api/areas")
async def list_areas():
    return _get_area_options()


# ── Landing page ──────────────────────────────────────────────────────

@app.get("/")
async def landing():
    return _render("landing.html")


# ── Health check ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cache_records": alert_cache.record_count,
        "last_refresh": str(alert_cache.last_refresh),
    }
