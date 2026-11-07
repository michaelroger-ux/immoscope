"""API web (FastAPI) et interface."""
from __future__ import annotations

import asyncio
import hmac
import html as html_lib
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config
from .db import get_db
from .location.categories import CATEGORIES, GROUP_LABELS, MODES
from .models import SearchQuery
from .pipeline import JOB, pending, start_processing, start_visual_checks
from .search.engine import merged_features, search
from .search.query_parser import parse_query
from .sources import demo, demo_art
from .sources.importers import ImportBlocked, import_html, import_records, import_url
from .vision.taxonomy import FEATURES, GROUPS

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Immoscope", version="0.1.0")
# Pas de CORS : aucun autre site ne doit pouvoir appeler l'API depuis le navigateur.
# Le marque-page passe par un formulaire (navigation), pas par un appel d'API.
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_PUBLIC_PATHS = {"/login", "/healthz", "/api/import/bookmarklet"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not auth.enabled():
        # En ligne sans mot de passe : tout reste fermé (seul le contrôle de santé répond).
        if config.ON_RENDER and path != "/healthz":
            msg = "Immoscope est verrouillé : ajoutez la variable IMMO_PASSWORD dans Render (Environment)."
            if path.startswith("/api/"):
                return JSONResponse({"detail": msg}, status_code=503)
            return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>Immoscope</title>"
                                f"<p style='font:16px system-ui;margin:20vh auto;max-width:520px;padding:0 16px'>"
                                f"{msg}</p>", status_code=503)
        return await call_next(request)
    if path in _PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    if auth.valid_session(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Connexion requise"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


_LOGIN_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Immoscope — connexion</title>
<style>
:root{{--bg:#f6f5f1;--surface:#fff;--text:#1d2321;--muted:#5f6763;--border:#dcd9d1;--accent:#2f6f5e;--no:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141816;--surface:#1c2220;--text:#e8ece9;--muted:#a1aba6;--border:#333c38;--accent:#6dbb9f;--no:#f28b82}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif}}
form{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:28px 24px;width:min(360px,calc(100vw - 32px));box-sizing:border-box}}
h1{{margin:0 0 4px;font-size:22px;color:var(--accent)}} p{{margin:0 0 18px;color:var(--muted)}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--border);border-radius:9px;font:inherit;background:var(--bg);color:var(--text)}}
button{{margin-top:12px;width:100%;padding:10px;border:0;border-radius:9px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:var(--no);margin:10px 0 0}}
</style></head><body><form method="post" action="/login">
<h1>Immoscope</h1><p>Recherche immobilière privée.</p>
<input type="password" name="password" placeholder="Mot de passe" autocomplete="current-password" autofocus required>
<button type="submit">Se connecter</button>{error}</form></body></html>"""


@app.get("/login", include_in_schema=False)
def login_page():
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_LOGIN_PAGE.format(error=""))


@app.post("/login", include_in_schema=False)
async def login(request: Request):
    form = await request.form()
    if not auth.check_password(str(form.get("password") or "")):
        await asyncio.sleep(1.0)  # ralentit les essais de mot de passe
        return HTMLResponse(_LOGIN_PAGE.format(error='<p class="err">Mot de passe incorrect.</p>'), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.MAX_AGE, httponly=True, samesite="lax",
                    secure=secure)
    return resp


@app.get("/logout", include_in_schema=False)
def logout():
    resp = RedirectResponse("/login" if auth.enabled() else "/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    db = get_db()
    p = pending(db)
    return {
        "claude": config.claude_available(),
        "model": config.CLAUDE_MODEL,
        "auth": auth.enabled(),
        "import_token": auth.import_token() if auth.enabled() else None,
        "storage": db.kind,
        # SQLite sur Render sans disque : les données disparaissent à chaque redémarrage
        "ephemeral_storage": config.ON_RENDER and db.kind == "sqlite" and not config.DATA_DIR_FROM_ENV,
        "count": db.count(),
        "pending": {k: len(v) for k, v in p.items()},
        "feature_groups": [
            {"key": g, "label": label,
             "features": [{"key": f.key, "label": f.label, "undesirable": f.undesirable} for f in FEATURES if f.group == g]}
            for g, label in GROUPS.items()
        ],
        "category_groups": [
            {"key": g, "label": label,
             "categories": [{"key": c.key, "label": c.label, "default_minutes": c.default_minutes,
                             "nuisance": c.nuisance, "radius_m": c.radius_m} for c in CATEGORIES if c.group == g]}
            for g, label in GROUP_LABELS.items()
        ],
        "modes": {k: v["label"] for k, v in MODES.items()},
        "job": JOB.snapshot(),
    }


class SearchRequest(BaseModel):
    text: Optional[str] = None
    query: Optional[SearchQuery] = None
    strict: bool = False
    use_claude: Optional[bool] = None


@app.post("/api/search")
def api_search(req: SearchRequest):
    parser = "manuel"
    if req.query is not None:
        query = req.query
    elif req.text:
        query, parser = parse_query(req.text, req.use_claude)
    else:
        query = SearchQuery()
    result = search(query, strict=req.strict)
    return {"query": query.model_dump(), "parser": parser, **result}


class ParseRequest(BaseModel):
    text: str
    use_claude: Optional[bool] = None


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    query, parser = parse_query(req.text, req.use_claude)
    return {"query": query.model_dump(), "parser": parser}


@app.get("/api/listings")
def api_listings():
    return {"listings": [x.model_dump() for x in get_db().list_listings()]}


@app.get("/api/listings/{listing_id:path}/detail")
def api_listing(listing_id: str):
    db = get_db()
    listing = db.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Annonce introuvable")
    vision = db.get_vision(listing_id)
    location = db.get_location(listing_id)
    feats = merged_features(listing, vision)
    by_key = {f.key: f for f in FEATURES}
    return {
        "listing": listing.model_dump(),
        "vision": vision.model_dump() if vision else None,
        "location": location.model_dump() if location else None,
        "features": [{**h.model_dump(), "label": by_key[k].label, "group": by_key[k].group}
                     for k, h in sorted(feats.items(), key=lambda kv: -kv[1].confidence) if k in by_key],
    }


@app.delete("/api/listings/{listing_id:path}")
def api_delete(listing_id: str):
    if not get_db().delete_listing(listing_id):
        raise HTTPException(404, "Annonce introuvable")
    return {"deleted": listing_id}


class UrlImport(BaseModel):
    url: str


@app.post("/api/import/url")
def api_import_url(req: UrlImport):
    try:
        result = import_url(req.url.strip())
    except ImportBlocked as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Impossible de lire la page : {exc}")
    if not result["found"]:
        raise HTTPException(422, "Aucune annonce reconnue sur cette page.")
    return result


class HtmlImport(BaseModel):
    html: str
    url: Optional[str] = None


@app.post("/api/import/html")
def api_import_html(req: HtmlImport):
    result = import_html(req.html, req.url)
    if not result["found"]:
        raise HTTPException(422, "Aucune annonce reconnue dans ce HTML.")
    return result


@app.post("/api/import/bookmarklet", response_class=HTMLResponse)
async def api_import_bookmarklet(request: Request):
    """Réception du marque-page : formulaire envoyé depuis la page de l'annonce, dans un nouvel onglet."""
    form = await request.form(max_part_size=30 * 1024 * 1024)
    url = str(form.get("url") or "") or None
    page = str(form.get("html") or "")
    token_ok = not auth.enabled() or hmac.compare_digest(str(form.get("token") or ""), auth.import_token())
    if not token_ok:
        ok, msg = False, "Marque-page non reconnu : réinstallez-le depuis Immoscope (menu Importer)."
    else:
        try:
            result = import_html(page, url)
            ok = result["found"] > 0
            msg = (f"{result['found']} annonce(s) importée(s) ({result['new']} nouvelle(s))." if ok
                   else "Aucune annonce reconnue sur cette page.")
        except Exception as exc:  # pragma: no cover
            ok, msg = False, f"Erreur : {exc}"
    color = "#1f7a4d" if ok else "#b3261e"
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Immoscope — import</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 16px;text-align:center">
<h1 style="color:{color};font-size:22px">{html_lib.escape(msg)}</h1>
<p style="color:#555">{html_lib.escape(url or "")}</p>
<p><a href="/" style="color:#2457c5">Ouvrir Immoscope</a> · <a href="#" onclick="window.close()">Fermer cet onglet</a></p>
</body></html>"""


@app.post("/api/import/file")
async def api_import_file(file: UploadFile = File(...)):
    content = await file.read()
    try:
        result = import_records(content, file.filename or "import.json")
    except Exception as exc:
        raise HTTPException(422, f"Fichier illisible : {exc}")
    return result


@app.post("/api/demo/load")
def api_demo_load():
    return {"loaded": demo.load_demo()}


@app.delete("/api/demo")
def api_demo_delete():
    return {"deleted": get_db().delete_source(demo.SOURCE)}


@app.get("/api/demo/image/{key}/{view}.svg", include_in_schema=False)
def api_demo_image(key: str, view: int):
    params = demo.drawing_params(key)
    if params is None:
        raise HTTPException(404)
    return Response(demo_art.render(params, view, key), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


class ProcessRequest(BaseModel):
    vision: bool = True
    location: bool = True
    ids: Optional[list[str]] = None
    force: bool = False


@app.post("/api/process")
def api_process(req: ProcessRequest):
    return start_processing(vision=req.vision, location=req.location, ids=req.ids, force=req.force)


@app.get("/api/process/status")
def api_process_status():
    return {"job": JOB.snapshot(), "pending": {k: len(v) for k, v in pending().items()}}


class VisualCheckRequest(BaseModel):
    question: str
    ids: list[str]


@app.post("/api/visual-check")
def api_visual_check(req: VisualCheckRequest):
    if not req.question.strip() or not req.ids:
        raise HTTPException(422, "Question et annonces requises.")
    return start_visual_checks(req.question.strip(), req.ids[:25])
