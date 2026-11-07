"""Repérage des caractéristiques dans le texte de l'annonce (sans IA, gratuit, instantané)."""
from __future__ import annotations

from ..models import FeatureHit, Listing
from ..textutil import find_terms
from .taxonomy import FEATURES

TEXT_CONFIDENCE = 0.6
_TERMS = {f.key: f.text_terms for f in FEATURES if f.text_terms}


def tag_text(listing: Listing) -> dict[str, FeatureHit]:
    text = f"{listing.title}\n{listing.description}"
    hits = find_terms(text, _TERMS)
    return {
        key: FeatureHit(key=key, confidence=TEXT_CONFIDENCE, source="texte",
                        evidence=f"« {expr} » mentionné dans l'annonce")
        for key, expr in hits.items()
    }
