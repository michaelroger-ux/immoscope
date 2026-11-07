"""Enrichissement « localisation » d'une annonce : coordonnées + points d'intérêt alentour."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..db import Database, get_db, now_iso
from ..models import Listing, LocationAnalysis
from .geocode import geocode
from .overpass import fetch_elements, summarize

log = logging.getLogger(__name__)


def resolve_coordinates(listing: Listing, db: Optional[Database] = None) -> Optional[tuple[float, float, str]]:
    """Coordonnées de l'annonce : celles fournies par le site, sinon géocodage de l'adresse / commune."""
    if listing.lat is not None and listing.lon is not None:
        return listing.lat, listing.lon, listing.geo_precision or "approchee"
    parts = [p for p in (listing.address, listing.postcode, listing.city) if p]
    if not parts:
        return None
    query = " ".join(parts)
    result = geocode(query, postcode=listing.postcode, db=db)
    if not result:
        return None
    precision = result["precision"] if listing.address else "commune"
    return result["lat"], result["lon"], precision


def coords_key(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def enrich_listing(listing: Listing, *, db: Optional[Database] = None, force: bool = False,
                   client: Optional[httpx.Client] = None) -> LocationAnalysis:
    db = db or get_db()
    coords = resolve_coordinates(listing, db)
    if coords is None:
        analysis = LocationAnalysis(listing_id=listing.id, enriched_at=now_iso(),
                                    error="Localisation inconnue : ni coordonnées, ni adresse, ni commune.")
        db.save_location(analysis, "")
        return analysis
    lat, lon, precision = coords
    key = coords_key(lat, lon)
    if not force:
        existing = db.get_location(listing.id)
        if existing and not existing.error and db.location_keys().get(listing.id) == key:
            return existing
    try:
        elements = fetch_elements(lat, lon, db=db, client=client)
    except httpx.HTTPError as exc:
        log.warning("Overpass indisponible pour %s : %s", listing.id, exc)
        analysis = LocationAnalysis(listing_id=listing.id, enriched_at=now_iso(), lat=lat, lon=lon,
                                    geo_precision=precision, error=f"Service OpenStreetMap indisponible : {exc}")
        db.save_location(analysis, "")
        return analysis
    summaries, pois = summarize(lat, lon, elements)
    analysis = LocationAnalysis(listing_id=listing.id, enriched_at=now_iso(), lat=lat, lon=lon,
                                geo_precision=precision, categories=summaries, pois=pois)
    db.save_location(analysis, key)
    return analysis
