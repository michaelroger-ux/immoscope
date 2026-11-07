"""Récupération des points d'intérêt OpenStreetMap autour d'un bien (API Overpass)."""
from __future__ import annotations

import logging
import time
from typing import Iterable, Optional

import httpx

from .. import config
from ..db import Database, get_db
from ..models import PoiSummary
from .categories import CATEGORIES, Category
from .geo import distance_to_polyline_m, haversine_m

log = logging.getLogger(__name__)


def build_query(lat: float, lon: float, categories: Iterable[Category] = CATEGORIES, timeout: int = 90) -> str:
    """Une seule requête Overpass pour toutes les catégories (chacune avec son propre rayon)."""
    cats = list(categories)
    point_parts, line_parts = [], []
    for cat in cats:
        for f in cat.filters:
            conds = "".join(c.ql() for c in f.conds)
            stmt = f"  {f.types}{conds}(around:{cat.radius_m},{lat:.6f},{lon:.6f});"
            (line_parts if cat.linear else point_parts).append(stmt)
    q = [f"[out:json][timeout:{timeout}];"]
    if point_parts:
        q += ["(", *point_parts, ")->.pois;", ".pois out center tags qt;"]
    if line_parts:
        q += ["(", *line_parts, ")->.lines;", ".lines out geom tags qt;"]
    return "\n".join(q)


_KEEP_TAGS = {"name", "brand", "ref", "operator"} | {c.key for cat in CATEGORIES for f in cat.filters for c in f.conds}


def _slim(el: dict) -> dict:
    """Garde seulement ce qui sert au calcul (le cache reste léger, même sur une base hébergée)."""
    out = {k: el[k] for k in ("type", "id", "lat", "lon", "center") if k in el}
    if "geometry" in el:
        out["geometry"] = [{"lat": round(g["lat"], 6), "lon": round(g["lon"], 6)} for g in el["geometry"] if "lat" in g]
    out["tags"] = {k: v for k, v in (el.get("tags") or {}).items() if k in _KEEP_TAGS}
    return out


def fetch_elements(lat: float, lon: float, *, db: Optional[Database] = None,
                   client: Optional[httpx.Client] = None, retries: int = 3) -> list[dict]:
    """Interroge Overpass (avec cache de 30 jours par position arrondie à ~10 m)."""
    db = db or get_db()
    key = f"overpass:v1:{lat:.4f},{lon:.4f}"
    cached = db.cache_get(key, max_age_days=30)
    if cached is not None:
        return cached
    query = build_query(lat, lon)
    own_client = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": config.USER_AGENT})
    try:
        delay = 5.0
        for attempt in range(retries):
            try:
                resp = client.post(config.OVERPASS_URL, data={"data": query})
                if resp.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                    log.info("Overpass occupé (%s), nouvel essai dans %.0fs", resp.status_code, delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                resp.raise_for_status()
                elements = [_slim(e) for e in resp.json().get("elements", [])]
                break
            except httpx.TransportError:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    finally:
        if own_client:
            client.close()
    db.cache_set(key, elements)
    return elements


def _name(tags: dict) -> Optional[str]:
    return tags.get("name") or tags.get("brand") or tags.get("ref") or tags.get("operator")


def summarize(lat: float, lon: float, elements: list[dict], categories: Iterable[Category] = CATEGORIES,
              keep_per_category: int = 5) -> tuple[dict[str, PoiSummary], list[dict]]:
    """Calcule, pour chaque catégorie, le point le plus proche et le nombre de points alentour."""
    cats = list(categories)
    per_cat: dict[str, list[dict]] = {c.key: [] for c in cats}
    for el in elements:
        tags = el.get("tags") or {}
        for cat in cats:
            if not cat.matches(el):
                continue
            if cat.linear:
                geom = el.get("geometry") or []
                pts = [(g["lat"], g["lon"]) for g in geom if "lat" in g]
                if not pts:
                    continue
                d, (plat, plon) = distance_to_polyline_m(lat, lon, pts)
            else:
                if "lat" in el:
                    plat, plon = el["lat"], el["lon"]
                elif "center" in el:
                    plat, plon = el["center"]["lat"], el["center"]["lon"]
                else:
                    continue
                d = haversine_m(lat, lon, plat, plon)
            if d > cat.radius_m * 1.05:
                continue
            per_cat[cat.key].append({"category": cat.key, "name": _name(tags), "lat": plat, "lon": plon,
                                     "distance_m": round(d), "osm": f"{el.get('type')}/{el.get('id')}"})

    summaries: dict[str, PoiSummary] = {}
    pois: list[dict] = []
    for cat in cats:
        items = sorted(per_cat[cat.key], key=lambda p: p["distance_m"])
        if cat.linear:
            # une même autoroute est découpée en dizaines de tronçons : on garde le plus proche par nom
            seen, dedup = set(), []
            for p in items:
                k = p["name"] or p["osm"]
                if k not in seen:
                    seen.add(k)
                    dedup.append(p)
            items = dedup
        nearest = items[0] if items else None
        summaries[cat.key] = PoiSummary(
            category=cat.key,
            found=nearest is not None,
            radius_m=cat.radius_m,
            distance_m=nearest["distance_m"] if nearest else None,
            name=nearest["name"] if nearest else None,
            lat=nearest["lat"] if nearest else None,
            lon=nearest["lon"] if nearest else None,
            count_500=sum(1 for p in items if p["distance_m"] <= 500),
            count_1000=sum(1 for p in items if p["distance_m"] <= 1000),
            count_radius=len(items),
        )
        pois.extend(items[:keep_per_category])
    return summaries, pois
