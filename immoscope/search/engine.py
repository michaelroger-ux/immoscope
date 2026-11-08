"""Moteur de recherche : filtres stricts + score de pertinence expliqué critère par critère."""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..db import Database, get_db
from ..location.categories import CATEGORIES_BY_KEY, MODES, travel_minutes
from ..location.geo import haversine_m
from ..location.geocode import geocode
from ..models import FeatureHit, Listing, LocationAnalysis, SearchQuery, VisionAnalysis
from ..textutil import normalize
from ..vision.keyword_tagger import tag_text
from ..vision.taxonomy import FEATURES_BY_KEY
from .geo_fr import department_of

log = logging.getLogger(__name__)

_STOPWORDS = set("avec sans pour dans des les une sur aux par est sont the and qui que tres plus moins entre".split())


@dataclass
class CriterionResult:
    kind: str  # visual | visual_free | proximity | keyword
    key: str
    label: str
    status: str  # ok | partiel | non | inconnu
    score: float
    detail: str
    required: bool = False
    image_index: Optional[int] = None


@dataclass
class SearchResult:
    listing: dict
    score: Optional[float]
    criteria: list[CriterionResult] = field(default_factory=list)
    best_image: Optional[int] = None
    analyzed: bool = False
    enriched: bool = False
    distance_km: Optional[float] = None
    warnings: list[str] = field(default_factory=list)
    # position pour la carte : celle du site, sinon celle calculée lors de l'analyse (géocodage)
    lat: Optional[float] = None
    lon: Optional[float] = None
    geo_precision: Optional[str] = None


def fmt_distance(m: float) -> str:
    if m < 1000:
        return f"{m:.0f} m"
    return f"{m / 1000:.0f} km" if m >= 10_000 else f"{m / 1000:.1f} km".replace(".", ",")


def merged_features(listing: Listing, vision: Optional[VisionAnalysis]) -> dict[str, FeatureHit]:
    """Caractéristiques vues sur les photos, complétées par celles mentionnées dans le texte."""
    features: dict[str, FeatureHit] = dict(vision.features) if vision and not vision.error else {}
    for key, hit in tag_text(listing).items():
        if key not in features:
            features[key] = hit
    return features


def _visual(c, features, analyzed: bool) -> CriterionResult:
    feat = FEATURES_BY_KEY.get(c.feature)
    label = feat.label if feat else c.feature
    hit = features.get(c.feature)
    present = hit is not None and hit.confidence >= 0.5
    src = {"photo": "photo", "texte": "texte", "demo": "photo"}.get(hit.source, "") if hit else ""
    if c.exclude:
        label = f"Pas de : {label.lower()}" if not label.lower().startswith("pas") else label
        if present:
            idx = hit.image_indices[0] if hit.image_indices else None
            return CriterionResult("visual", c.feature, label, "non", 0.0, hit.evidence or "Détecté", c.required, idx)
        if analyzed:
            return CriterionResult("visual", c.feature, label, "ok", 1.0, "Non détecté sur les photos", c.required)
        return CriterionResult("visual", c.feature, label, "inconnu", 0.5, "Photos pas encore analysées", c.required)
    if present:
        status = "ok" if hit.confidence >= 0.75 or hit.source == "texte" else "partiel"
        idx = hit.image_indices[0] if hit.image_indices else None
        detail = hit.evidence or ("Visible sur les photos" if src == "photo" else "Mentionné dans l'annonce")
        if src == "photo" and idx is not None:
            detail += f" (photo {idx + 1}, {round(hit.confidence * 100)} %)"
        return CriterionResult("visual", c.feature, label, status, hit.confidence, detail, c.required, idx)
    if analyzed:
        return CriterionResult("visual", c.feature, label, "non", 0.0, "Non visible sur les photos analysées", c.required)
    return CriterionResult("visual", c.feature, label, "inconnu", 0.0,
                           "Photos pas encore analysées (ni mentionné dans le texte)", c.required)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", normalize(text)) if len(w) > 3 and w not in _STOPWORDS}


def _visual_free(c, listing: Listing, vision: Optional[VisionAnalysis], checks: dict) -> CriterionResult:
    label = f"Photo : {c.question}"
    check = checks.get((listing.id, c.question))
    if check and not check.get("error"):
        idx = check["image_indices"][0] if check.get("image_indices") else None
        if check.get("match"):
            status = "ok" if check.get("confidence", 0) >= 0.7 else "partiel"
            return CriterionResult("visual_free", c.question, label, status, float(check.get("confidence", 0.7)),
                                   check.get("evidence") or "Vérifié sur les photos", c.required, idx)
        return CriterionResult("visual_free", c.question, label, "non", 0.0,
                               check.get("evidence") or "Non visible sur les photos", c.required)
    wanted = _words(c.question)
    corpus = f"{listing.title} {listing.description}"
    if vision and not vision.error:
        corpus += " " + vision.summary + " " + " ".join(i.description for i in vision.images)
    if wanted:
        ratio = len(wanted & _words(corpus)) / len(wanted)
        if ratio >= 0.6:
            return CriterionResult("visual_free", c.question, label, "partiel", 0.5,
                                   "Termes retrouvés dans l'annonce ou la description des photos — à vérifier",
                                   c.required)
    return CriterionResult("visual_free", c.question, label, "inconnu", 0.0,
                           "Pas encore vérifié sur les photos", c.required)


def _proximity(c, loc: Optional[LocationAnalysis]) -> CriterionResult:
    cat = CATEGORIES_BY_KEY.get(c.category)
    if cat is None:
        return CriterionResult("proximity", c.category, c.category, "inconnu", 0.0, "Catégorie inconnue", c.required)
    mode = MODES.get(c.mode, MODES["pied"])
    if c.min_meters is not None:
        label = f"Loin : {cat.label.lower()} (> {fmt_distance(c.min_meters)})"
    elif c.max_minutes is not None:
        label = f"{cat.label} ≤ {c.max_minutes:g} min {mode['label']}"
    elif c.max_meters is not None:
        label = f"{cat.label} ≤ {fmt_distance(c.max_meters)}"
    else:
        label = cat.label
    if loc is None or loc.error or c.category not in loc.categories:
        detail = loc.error if loc and loc.error else "Localisation pas encore analysée"
        return CriterionResult("proximity", c.category, label, "inconnu", 0.0, detail, c.required)
    s = loc.categories[c.category]
    approx = " — position approximative (centre de la commune)" if loc.geo_precision == "commune" else ""
    name = s.name or cat.label

    if c.min_meters is not None:
        if not s.found or s.distance_m is None:
            return CriterionResult("proximity", c.category, label, "ok", 1.0,
                                   f"Aucune à moins de {fmt_distance(s.radius_m)}{approx}", c.required)
        d = s.distance_m
        if d >= c.min_meters:
            return CriterionResult("proximity", c.category, label, "ok", 1.0, f"{name} à {fmt_distance(d)}{approx}",
                                   c.required)
        score = max(0.0, d / c.min_meters)
        return CriterionResult("proximity", c.category, label, "partiel" if score >= 0.5 else "non", round(score, 2),
                               f"{name} à seulement {fmt_distance(d)}{approx}", c.required)

    if not s.found or s.distance_m is None:
        return CriterionResult("proximity", c.category, label, "non", 0.0,
                               f"Rien trouvé à moins de {fmt_distance(s.radius_m)}{approx}", c.required)
    d = s.distance_m
    minutes = travel_minutes(d, c.mode)
    ratio = 0.0
    if c.max_minutes:
        ratio = max(ratio, minutes / c.max_minutes)
    if c.max_meters:
        ratio = max(ratio, d / c.max_meters)
    if not c.max_minutes and not c.max_meters:
        ratio = minutes / cat.default_minutes
    score = 1.0 if ratio <= 1 else max(0.0, 2.0 - ratio)
    status = "ok" if ratio <= 1 else "partiel" if score > 0 else "non"
    dense = cat.key in ("commerces", "restaurants", "bus", "boulangerie", "ecole")
    extra = f", {s.count_1000} à moins d'1 km" if dense and s.count_1000 > 1 else ""
    detail = f"{name} à {fmt_distance(d)} (~{minutes:.0f} min {mode['label']}){extra}{approx}"
    return CriterionResult("proximity", c.category, label, status, round(score, 2), detail, c.required)


def _keyword(word: str, listing: Listing) -> CriterionResult:
    found = normalize(word) in normalize(f"{listing.title} {listing.description}")
    return CriterionResult("keyword", word, f"« {word} »", "ok" if found else "non", 1.0 if found else 0.0,
                           "Présent dans l'annonce" if found else "Absent du texte de l'annonce")


_WEIGHTS = {"visual": 1.0, "visual_free": 1.0, "proximity": 1.0, "keyword": 0.7}


def _resolve_center(query: SearchQuery, db: Database) -> tuple[Optional[tuple[float, float]], Optional[str]]:
    area = query.area
    if area.lat is not None and area.lon is not None:
        return (area.lat, area.lon), None
    if not area.near:
        return None, None
    try:
        g = geocode(area.near, db=db)
    except Exception as exc:  # pragma: no cover - réseau
        log.warning("Géocodage du secteur impossible : %s", exc)
        g = None
    if not g:
        return None, f"Lieu « {area.near} » introuvable : le filtre géographique est ignoré."
    area.lat, area.lon = g["lat"], g["lon"]
    return (g["lat"], g["lon"]), None


def _outside(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    if value is None:
        return False
    return (lo is not None and value < lo) or (hi is not None and value > hi)


def search(query: SearchQuery, *, db: Optional[Database] = None, strict: bool = False) -> dict:
    db = db or get_db()
    listings = db.list_listings()
    visions = db.all_vision()
    locations = db.all_locations()
    imported = db.imported_at()
    checks = db.visual_checks([f.question for f in query.visual_free])
    center, area_warning = _resolve_center(query, db)
    radius_m = (query.area.radius_km or 10) * 1000

    results: list[SearchResult] = []
    excluded = 0
    for listing in listings:
        if query.property_types and listing.property_type and listing.property_type not in query.property_types:
            excluded += 1
            continue
        if (_outside(listing.price, query.price_min, query.price_max)
                or _outside(listing.surface, query.surface_min, query.surface_max)
                or _outside(listing.land_surface, query.land_min, query.land_max)
                or (query.rooms_min and listing.rooms is not None and listing.rooms < query.rooms_min)
                or (query.bedrooms_min and listing.bedrooms is not None and listing.bedrooms < query.bedrooms_min)):
            excluded += 1
            continue
        loc = locations.get(listing.id)
        vision = visions.get(listing.id)
        lat = listing.lat if listing.lat is not None else (loc.lat if loc else None)
        lon = listing.lon if listing.lon is not None else (loc.lon if loc else None)
        distance_km = None
        if center:
            if lat is None or lon is None:
                excluded += 1
                continue
            d = haversine_m(center[0], center[1], lat, lon)
            if d > radius_m:
                excluded += 1
                continue
            distance_km = round(d / 1000, 1)
        if query.area.departments:
            dep = department_of(listing.postcode)
            if dep is None or dep not in query.area.departments:
                excluded += 1
                continue

        analyzed = vision is not None and not vision.error
        features = merged_features(listing, vision)
        crit: list[CriterionResult] = []
        crit += [_visual(c, features, analyzed) for c in query.visual]
        crit += [_visual_free(c, listing, vision, checks) for c in query.visual_free]
        crit += [_proximity(c, loc) for c in query.proximity]
        crit += [_keyword(k, listing) for k in query.keywords]

        if any(c.required and c.status not in ("ok", "partiel") for c in crit):
            excluded += 1
            continue
        if strict and any(c.status not in ("ok", "partiel") for c in crit):
            excluded += 1
            continue

        score = None
        if crit:
            weights = [_WEIGHTS[c.kind] * (1.5 if c.required else 1.0) for c in crit]
            score = round(sum(w * c.score for w, c in zip(weights, crit)) / sum(weights), 3)

        matched = [c for c in crit if c.kind in ("visual", "visual_free") and c.status in ("ok", "partiel")
                   and c.image_index is not None]
        best_image = max(matched, key=lambda c: c.score).image_index if matched else None

        warnings = []
        if loc and loc.geo_precision == "commune":
            warnings.append("Adresse non précisée : distances calculées depuis le centre de la commune.")
        results.append(SearchResult(
            listing=listing.model_dump(), score=score, criteria=crit, best_image=best_image, analyzed=analyzed,
            enriched=loc is not None and not loc.error, distance_km=distance_km, warnings=warnings,
            lat=lat, lon=lon,
            geo_precision=listing.geo_precision if listing.lat is not None else (loc.geo_precision if loc else None),
        ))

    def recency(r: SearchResult) -> str:
        return r.listing.get("published_at") or imported.get(r.listing["id"], "")

    if query.sort == "price_asc":
        results.sort(key=lambda r: (r.listing.get("price") is None, r.listing.get("price") or 0))
    elif query.sort == "price_desc":
        results.sort(key=lambda r: -(r.listing.get("price") or 0))
    elif query.sort == "surface_desc":
        results.sort(key=lambda r: -(r.listing.get("surface") or 0))
    elif query.sort == "recent":
        results.sort(key=recency, reverse=True)
    else:
        results.sort(key=recency, reverse=True)
        results.sort(key=lambda r: r.score if r.score is not None else -1, reverse=True)

    return {
        "results": [asdict(r) for r in results],
        "total": len(listings),
        "matched": len(results),
        "excluded": excluded,
        "not_analyzed": sum(1 for r in results if not r.analyzed),
        "not_enriched": sum(1 for r in results if not r.enriched),
        "area_center": {"lat": center[0], "lon": center[1], "radius_km": query.area.radius_km or 10} if center else None,
        "warnings": [w for w in [area_warning] if w],
    }
