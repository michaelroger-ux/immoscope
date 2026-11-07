"""Modèles de données partagés."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PropertyType = Literal["maison", "appartement", "terrain", "immeuble", "autre"]
TravelMode = Literal["pied", "velo", "voiture"]


class Listing(BaseModel):
    id: str  # "<source>:<identifiant>"
    source: str
    url: Optional[str] = None
    title: str = ""
    description: str = ""
    price: Optional[float] = None
    surface: Optional[float] = None  # m² habitables
    land_surface: Optional[float] = None  # m² de terrain
    rooms: Optional[int] = None
    bedrooms: Optional[int] = None
    property_type: Optional[PropertyType] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    # "exacte", "approchee" (rue / position floutée par le site) ou "commune" (centre de la commune)
    geo_precision: Optional[str] = None
    images: list[str] = Field(default_factory=list)
    published_at: Optional[str] = None
    raw: dict = Field(default_factory=dict, exclude=True)


class FeatureHit(BaseModel):
    key: str
    confidence: float
    source: Literal["photo", "texte", "demo"] = "photo"
    image_indices: list[int] = Field(default_factory=list)
    evidence: str = ""


class ImageNote(BaseModel):
    index: int
    kind: str = "autre"
    description: str = ""


class VisionAnalysis(BaseModel):
    listing_id: str
    model: str = ""
    analyzed_at: Optional[str] = None
    features: dict[str, FeatureHit] = Field(default_factory=dict)
    images: list[ImageNote] = Field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


class PoiSummary(BaseModel):
    """Le point d'intérêt le plus proche d'une catégorie, et la densité autour du bien."""
    category: str
    found: bool
    radius_m: int
    distance_m: Optional[float] = None
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    count_500: int = 0
    count_1000: int = 0
    count_radius: int = 0


class LocationAnalysis(BaseModel):
    listing_id: str
    enriched_at: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    geo_precision: Optional[str] = None
    categories: dict[str, PoiSummary] = Field(default_factory=dict)
    pois: list[dict] = Field(default_factory=list)  # pour la carte : {category, name, lat, lon, distance_m}
    error: Optional[str] = None


# ----------------------------------------------------------------------------------
# Requête de recherche structurée
# ----------------------------------------------------------------------------------

class VisualCriterion(BaseModel):
    feature: str = Field(description="Clé de caractéristique visuelle (taxonomie fermée)")
    required: bool = Field(False, description="Vrai si l'utilisateur l'exige explicitement")
    exclude: bool = Field(False, description="Vrai si l'utilisateur veut l'éviter (ex. vis-à-vis)")


class FreeVisualCriterion(BaseModel):
    """Critère visuel hors taxonomie (« volets bleus », « glycine sur la façade »…)."""
    question: str = Field(description="Le critère reformulé en une courte phrase vérifiable sur photo")
    required: bool = False


class ProximityCriterion(BaseModel):
    category: str = Field(description="Clé de catégorie de point d'intérêt")
    max_minutes: Optional[float] = Field(None, description="Temps de trajet maximal souhaité")
    mode: TravelMode = "pied"
    max_meters: Optional[float] = Field(None, description="Distance maximale (vol d'oiseau) en mètres")
    min_meters: Optional[float] = Field(None, description="Distance minimale, pour s'éloigner d'une nuisance")
    required: bool = False


class SearchArea(BaseModel):
    near: Optional[str] = Field(None, description="Ville ou lieu de référence, ex. « Rennes »")
    radius_km: Optional[float] = Field(None, description="Rayon autour du lieu de référence")
    departments: list[str] = Field(default_factory=list, description="Codes de départements, ex. ['35', '22']")
    lat: Optional[float] = None
    lon: Optional[float] = None


class SearchQuery(BaseModel):
    text: Optional[str] = None
    property_types: list[PropertyType] = Field(default_factory=list)
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    surface_min: Optional[float] = None
    surface_max: Optional[float] = None
    land_min: Optional[float] = None
    land_max: Optional[float] = None
    rooms_min: Optional[int] = None
    bedrooms_min: Optional[int] = None
    area: SearchArea = Field(default_factory=SearchArea)
    visual: list[VisualCriterion] = Field(default_factory=list)
    visual_free: list[FreeVisualCriterion] = Field(default_factory=list)
    proximity: list[ProximityCriterion] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sort: Literal["score", "price_asc", "price_desc", "recent", "surface_desc"] = "score"
