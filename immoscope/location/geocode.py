"""Géocodage d'adresses / communes.

1. Géoplateforme IGN (adresses françaises, gratuit, sans clé — même format que l'ex-API Adresse) ;
2. Nominatim (OpenStreetMap) en secours, pour les autres pays. Limite : 1 requête/seconde.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import httpx

from .. import config
from ..db import Database, get_db
from ..textutil import normalize

log = logging.getLogger(__name__)

_PRECISION_FR = {
    "housenumber": "exacte",
    "street": "approchee",
    "locality": "approchee",
    "municipality": "commune",
}

_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


def _http() -> httpx.Client:
    return httpx.Client(timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.USER_AGENT})


def _geocode_fr(client: httpx.Client, query: str, postcode: Optional[str]) -> Optional[dict]:
    params = {"q": query, "limit": 1, "index": "address"}
    if postcode:
        params["postcode"] = postcode
    resp = client.get(config.GEOCODER_FR_URL, params=params)
    resp.raise_for_status()
    feats = resp.json().get("features") or []
    if not feats:
        return None
    f = feats[0]
    props = f.get("properties") or {}
    if float(props.get("score", 0)) < 0.4:
        return None
    lon, lat = f["geometry"]["coordinates"][:2]
    return {
        "lat": float(lat),
        "lon": float(lon),
        "label": props.get("label") or query,
        "precision": _PRECISION_FR.get(props.get("type", ""), "approchee"),
        "provider": "geoplateforme",
    }


def _geocode_nominatim(client: httpx.Client, query: str) -> Optional[dict]:
    global _nominatim_last
    with _nominatim_lock:
        wait = 1.1 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        resp = client.get(config.NOMINATIM_URL, params={"q": query, "format": "jsonv2", "limit": 1,
                                                        "accept-language": "fr"})
        _nominatim_last = time.monotonic()
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    r = results[0]
    addresstype = r.get("addresstype") or r.get("type") or ""
    if addresstype in ("house", "building"):
        precision = "exacte"
    elif addresstype in ("road", "street", "hamlet", "neighbourhood", "suburb"):
        precision = "approchee"
    else:
        precision = "commune"
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "label": r.get("display_name", query),
            "precision": precision, "provider": "nominatim"}


def geocode(query: str, postcode: Optional[str] = None, *, db: Optional[Database] = None,
            client: Optional[httpx.Client] = None) -> Optional[dict]:
    """Renvoie {lat, lon, label, precision, provider} ou None. Résultats mis en cache."""
    query = (query or "").strip()
    if not query:
        return None
    db = db or get_db()
    key = f"geocode:{normalize(query)}|{postcode or ''}"
    cached = db.cache_get(key, max_age_days=180)
    if cached is not None:
        return cached or None

    own_client = client is None
    client = client or _http()
    result = None
    try:
        try:
            result = _geocode_fr(client, query, postcode)
        except httpx.HTTPError as exc:
            log.warning("Géoplateforme indisponible (%s), essai Nominatim", exc)
        if result is None:
            try:
                result = _geocode_nominatim(client, f"{query} {postcode or ''}".strip())
            except httpx.HTTPError as exc:
                log.warning("Nominatim indisponible : %s", exc)
                return None  # ne pas mettre en cache une panne réseau
    finally:
        if own_client:
            client.close()
    db.cache_set(key, result or {})
    return result
