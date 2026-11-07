"""Points d'entrée pour faire entrer des annonces dans Immoscope.

- `import_url` : télécharge une page d'annonce (ou de résultats) et en extrait les annonces ;
- `import_html` : même chose à partir d'un HTML déjà obtenu (marque-page « Envoyer à Immoscope »,
  copier-coller du code source) — utile pour les sites qui bloquent les requêtes automatiques ;
- `import_records` : fichiers JSON / CSV (export d'un autre outil, saisie manuelle…).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from typing import Any, Optional

import httpx

from .. import config
from ..db import Database, get_db
from ..models import Listing
from .extract import _source_name, extract_listings, map_object

log = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
}

_BLOCK_MARKERS = ("captcha-delivery.com", "datadome", "cf-chl", "challenge-platform", "px-captcha",
                  "access denied", "accès refusé", "are you a robot", "êtes-vous un robot")


class ImportBlocked(Exception):
    """Le site refuse les requêtes automatiques : passer par le marque-page ou le copier-coller."""


def fetch_html(url: str, client: Optional[httpx.Client] = None) -> str:
    own = client is None
    client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, headers=BROWSER_HEADERS, follow_redirects=True)
    try:
        resp = client.get(url)
    finally:
        if own:
            client.close()
    head = resp.text[:20000].lower()
    if resp.status_code in (401, 403, 429) or (len(resp.text) < 15000 and any(m in head for m in _BLOCK_MARKERS)):
        raise ImportBlocked(
            f"{_source_name(url)} bloque les requêtes automatiques (HTTP {resp.status_code}). "
            "Ouvrez l'annonce dans votre navigateur puis utilisez le marque-page « Envoyer à Immoscope », "
            "ou collez le code source de la page dans la fenêtre d'import."
        )
    resp.raise_for_status()
    return resp.text


def save_listings(listings: list[Listing], db: Optional[Database] = None) -> dict:
    db = db or get_db()
    new = sum(1 for listing in listings if db.upsert_listing(listing))
    return {"found": len(listings), "new": new, "updated": len(listings) - new, "ids": [x.id for x in listings]}


def import_html(html: str, url: Optional[str] = None, db: Optional[Database] = None) -> dict:
    listings = extract_listings(html, url)
    return save_listings(listings, db)


def import_url(url: str, db: Optional[Database] = None, client: Optional[httpx.Client] = None) -> dict:
    return import_html(fetch_html(url, client), url, db)


def _record_to_listing(rec: dict, default_source: str) -> Optional[Listing]:
    """Accepte le format Immoscope (champs de `Listing`) ou n'importe quel objet « annonce »."""
    if "title" in rec and ("id" in rec or "url" in rec) and set(rec) <= set(Listing.model_fields) | {"raw"}:
        data = dict(rec)
        for k in ("price", "surface", "land_surface", "lat", "lon"):
            if isinstance(data.get(k), str):
                data[k] = float(data[k].replace(",", ".").replace(" ", "")) if data[k].strip() else None
        for k in ("rooms", "bedrooms"):
            if isinstance(data.get(k), str):
                data[k] = int(float(data[k])) if data[k].strip() else None
        if isinstance(data.get("images"), str):
            data["images"] = [u.strip() for u in data["images"].replace("|", "\n").splitlines() if u.strip()]
        data.setdefault("source", _source_name(data.get("url")) if data.get("url") else default_source)
        if not data.get("id"):
            data["id"] = f"{data['source']}:{hashlib.sha1(str(data.get('url')).encode()).hexdigest()[:12]}"
        elif ":" not in str(data["id"]):
            data["id"] = f"{data['source']}:{data['id']}"
        data = {k: v for k, v in data.items() if v not in ("",)}
        return Listing(**data)
    return map_object(rec, rec.get("url"), None)


def import_records(content: bytes | str, filename: str = "import.json", db: Optional[Database] = None) -> dict:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    records: list[Any]
    if filename.lower().endswith(".csv"):
        sample = text[:2000]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
        records = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    else:
        data = json.loads(text)
        records = data if isinstance(data, list) else data.get("listings") or data.get("results") or [data]
    listings = []
    for rec in records:
        if isinstance(rec, dict):
            try:
                listing = _record_to_listing(rec, "fichier")
            except (ValueError, TypeError) as exc:
                log.warning("Enregistrement ignoré : %s", exc)
                continue
            if listing:
                listings.append(listing)
    return save_listings(listings, db)
