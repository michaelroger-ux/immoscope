"""Analyse des photos d'annonces avec Claude (vision).

Deux usages :
- `analyze_listing` : repère, sur toutes les photos d'une annonce, les caractéristiques de la
  taxonomie fermée (façade en pierre, terrain arboré, vue mer…), avec confiance et justification ;
- `visual_check` : vérifie un critère libre (« volets bleus », « glycine ») sur les photos.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

import anthropic
import httpx
from pydantic import BaseModel

from .. import config
from ..db import Database, get_db, now_iso
from ..models import FeatureHit, ImageNote, Listing, VisionAnalysis
from .images import image_block, images_hash
from .taxonomy import FEATURE_KEYS, IMAGE_KINDS, taxonomy_prompt

log = logging.getLogger(__name__)

FeatureKey = Literal[FEATURE_KEYS]  # type: ignore[valid-type]
ImageKind = Literal[IMAGE_KINDS]  # type: ignore[valid-type]

# Réessaie automatiquement sur un autre modèle si une requête est refusée par les garde-fous.
_FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class _FeatureOut(BaseModel):
    key: FeatureKey
    confidence: float
    image_indices: list[int]
    evidence: str


class _ImageOut(BaseModel):
    index: int
    kind: ImageKind
    description: str


class _AnalysisOut(BaseModel):
    images: list[_ImageOut]
    features: list[_FeatureOut]
    summary: str


class _CheckOut(BaseModel):
    match: bool
    confidence: float
    image_indices: list[int]
    evidence: str


SYSTEM_PROMPT = f"""Tu analyses les photos d'annonces immobilières (principalement en France) pour \
alimenter un moteur de recherche. L'utilisateur cherche des biens selon ce qui se voit sur les photos : \
matériaux de façade, style, caractéristiques du terrain et du paysage, intérieur, état.

Pour chaque annonce tu reçois des photos numérotées à partir de 0.

1. Pour chaque photo, donne son type ({", ".join(IMAGE_KINDS)}) et une description factuelle d'une phrase \
(ce qu'on voit, matériaux, végétation, relief, vue).
2. Liste les caractéristiques de la taxonomie ci-dessous qui sont visibles sur au moins une photo, avec :
   - confidence : 0,9 ou plus si c'est clairement visible, 0,6 à 0,8 si c'est probable mais partiel ou \
lointain. Ne liste pas une caractéristique en dessous de 0,5 ;
   - image_indices : les photos où on la voit ;
   - evidence : une justification courte et concrète (« moellons de granit apparents sur la façade avant »).
   Base-toi uniquement sur les photos, pas sur ce que l'annonce pourrait dire. Les plans et les photos \
d'intérieur ne permettent pas de juger la façade ; une photo de détail ne suffit pas à juger tout le terrain.
3. summary : 1 à 2 phrases décrivant le bien et son environnement tels qu'ils apparaissent sur les photos.

# Taxonomie
{taxonomy_prompt()}
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _photo_content(listing: Listing, http: httpx.Client, max_images: int) -> tuple[list[dict], list[str]]:
    content: list[dict] = []
    used: list[str] = []
    for url in listing.images:
        if len(used) >= max_images:
            break
        block = image_block(url, http, referer=listing.url)
        if block is None:
            continue
        content.append({"type": "text", "text": f"Photo {len(used)} :"})
        content.append(block)
        used.append(url)
    return content, used


def _describe(listing: Listing) -> str:
    where = ", ".join(p for p in (listing.city, listing.postcode) if p)
    return f"Annonce : {listing.title or 'sans titre'}" + (f" ({where})" if where else "")


def _api_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "Clé API Claude invalide ou absente."
    if isinstance(exc, anthropic.RateLimitError):
        return "Limite de débit de l'API Claude atteinte, réessayez plus tard."
    if isinstance(exc, anthropic.BadRequestError):
        return f"Requête refusée par l'API Claude : {exc.message}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Erreur de l'API Claude ({exc.status_code}) : {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "API Claude injoignable (réseau)."
    return str(exc)


def analyze_listing(listing: Listing, *, db: Optional[Database] = None, force: bool = False,
                    client: Optional[anthropic.Anthropic] = None,
                    http: Optional[httpx.Client] = None) -> VisionAnalysis:
    """Analyse les photos d'une annonce et enregistre le résultat."""
    db = db or get_db()
    urls = [u for u in listing.images if u.startswith(("http://", "https://"))][: config.VISION_MAX_IMAGES]
    h = images_hash(urls)
    if not force:
        existing = db.get_vision(listing.id)
        if existing and not existing.error and db.vision_hashes().get(listing.id) == h:
            return existing
    if not urls:
        analysis = VisionAnalysis(listing_id=listing.id, analyzed_at=now_iso(), error="Aucune photo à analyser.")
        db.save_vision(analysis, h)
        return analysis

    own_http = http is None
    http = http or httpx.Client(timeout=config.HTTP_TIMEOUT)
    try:
        photos, used = _photo_content(listing.model_copy(update={"images": urls}), http, config.VISION_MAX_IMAGES)
    finally:
        if own_http:
            http.close()

    client = client or _client()
    try:
        response = client.beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": [
                *photos,
                {"type": "text", "text": f"{_describe(listing)}\nAnalyse ces {len(used)} photos."},
            ]}],
            output_format=_AnalysisOut,
            output_config={"effort": config.VISION_EFFORT},
            **_FALLBACK,
        )
    except anthropic.APIError as exc:
        log.warning("Analyse visuelle échouée pour %s : %s", listing.id, exc)
        analysis = VisionAnalysis(listing_id=listing.id, analyzed_at=now_iso(), model=config.CLAUDE_MODEL,
                                  error=_api_error(exc))
        db.save_vision(analysis, h)
        return analysis

    if response.stop_reason == "refusal" or response.parsed_output is None:
        reason = "refus du modèle" if response.stop_reason == "refusal" else f"réponse incomplète ({response.stop_reason})"
        analysis = VisionAnalysis(listing_id=listing.id, analyzed_at=now_iso(), model=response.model,
                                  error=f"Analyse impossible : {reason}.")
        db.save_vision(analysis, h)
        return analysis

    out: _AnalysisOut = response.parsed_output
    features: dict[str, FeatureHit] = {}
    for f in out.features:
        conf = max(0.0, min(1.0, f.confidence))
        if conf < 0.5:
            continue
        indices = sorted({i for i in f.image_indices if 0 <= i < len(used)})
        if f.key not in features or conf > features[f.key].confidence:
            features[f.key] = FeatureHit(key=f.key, confidence=round(conf, 2), source="photo",
                                         image_indices=indices, evidence=f.evidence)
    analysis = VisionAnalysis(
        listing_id=listing.id,
        model=response.model,
        analyzed_at=now_iso(),
        features=features,
        images=[ImageNote(index=i.index, kind=i.kind, description=i.description)
                for i in out.images if 0 <= i.index < len(used)],
        summary=out.summary,
    )
    db.save_vision(analysis, h)
    return analysis


def visual_check(listing: Listing, question: str, *, db: Optional[Database] = None,
                 client: Optional[anthropic.Anthropic] = None, http: Optional[httpx.Client] = None) -> dict:
    """Vérifie un critère visuel libre sur les photos d'une annonce. Résultat mis en cache."""
    db = db or get_db()
    question = question.strip()
    cached = db.visual_checks([question]).get((listing.id, question))
    if cached and not cached.get("error"):
        return cached
    urls = [u for u in listing.images if u.startswith(("http://", "https://"))][: config.VISION_MAX_IMAGES]
    if not urls:
        result = {"match": False, "confidence": 0.0, "image_indices": [], "evidence": "Aucune photo.",
                  "checked_at": now_iso()}
        db.save_visual_check(listing.id, question, result)
        return result
    own_http = http is None
    http = http or httpx.Client(timeout=config.HTTP_TIMEOUT)
    try:
        photos, used = _photo_content(listing.model_copy(update={"images": urls}), http, config.VISION_MAX_IMAGES)
    finally:
        if own_http:
            http.close()
    client = client or _client()
    try:
        response = client.beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=4000,
            system="Tu vérifies, pour un moteur de recherche immobilier, si un critère est visible sur les "
                   "photos d'une annonce. Réponds uniquement d'après ce qui se voit. match = vrai seulement si "
                   "le critère est visible sur au moins une photo ; confidence entre 0 et 1 ; image_indices = "
                   "photos concernées (numérotées à partir de 0) ; evidence = une phrase de justification.",
            messages=[{"role": "user", "content": [
                *photos,
                {"type": "text", "text": f"{_describe(listing)}\nCritère à vérifier : « {question} »"},
            ]}],
            output_format=_CheckOut,
            output_config={"effort": "low"},
            **_FALLBACK,
        )
    except anthropic.APIError as exc:
        return {"match": False, "confidence": 0.0, "image_indices": [], "evidence": "", "error": _api_error(exc)}
    out = response.parsed_output
    if response.stop_reason == "refusal" or out is None:
        return {"match": False, "confidence": 0.0, "image_indices": [], "evidence": "",
                "error": "Vérification impossible."}
    result = {
        "match": bool(out.match),
        "confidence": round(max(0.0, min(1.0, out.confidence)), 2),
        "image_indices": sorted({i for i in out.image_indices if 0 <= i < len(used)}),
        "evidence": out.evidence,
        "checked_at": now_iso(),
    }
    db.save_visual_check(listing.id, question, result)
    return result
