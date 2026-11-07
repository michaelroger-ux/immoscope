"""Catégories de points d'intérêt (OpenStreetMap) utilisées pour la recherche par localisation."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Cond:
    key: str
    op: str  # "=", "!=", "~" (expression régulière complète)
    value: str

    def ql(self) -> str:
        if self.op == "~":
            return f'["{self.key}"~"^({self.value})$"]'
        return f'["{self.key}"{self.op}"{self.value}"]'

    def matches(self, tags: dict) -> bool:
        actual = tags.get(self.key)
        if self.op == "=":
            return actual == self.value
        if self.op == "!=":
            return actual != self.value
        if self.op == "~":
            return actual is not None and re.fullmatch(f"({self.value})", actual) is not None
        raise ValueError(self.op)


@dataclass(frozen=True)
class OsmFilter:
    types: str  # "node", "way", "nwr"
    conds: tuple[Cond, ...]

    def matches(self, element: dict) -> bool:
        etype = element.get("type")
        if self.types != "nwr" and etype != self.types:
            return False
        tags = element.get("tags") or {}
        return all(c.matches(tags) for c in self.conds)


def F(types: str, *conds: tuple[str, str, str]) -> OsmFilter:
    return OsmFilter(types, tuple(Cond(*c) for c in conds))


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: str
    radius_m: int  # rayon de recherche autour du bien
    filters: tuple[OsmFilter, ...]
    query_terms: tuple[str, ...]
    default_minutes: float = 10.0
    nuisance: bool = False  # True : on cherche plutôt à s'en éloigner (autoroute, voie ferrée)
    linear: bool = False  # True : objets linéaires, distance calculée sur la géométrie

    def matches(self, element: dict) -> bool:
        return any(f.matches(element) for f in self.filters)


CATEGORIES: tuple[Category, ...] = (
    # Transports
    Category("gare", "Gare (train, TER, RER)", "transport", 12000,
             (F("nwr", ("railway", "~", "station|halt"), ("station", "!=", "subway"),
                ("usage", "!=", "tourism")),),
             ("gare", "gare sncf", "train", "ter", "rer", "tgv", "transilien"), 15),
    Category("metro", "Métro", "transport", 2500,
             (F("nwr", ("railway", "=", "station"), ("station", "=", "subway")),),
             ("metro", "station de metro"), 10),
    Category("tram", "Tramway", "transport", 2500,
             (F("node", ("railway", "=", "tram_stop")),),
             ("tram", "tramway"), 10),
    Category("bus", "Arrêt de bus", "transport", 1200,
             (F("node", ("highway", "=", "bus_stop")),),
             ("bus", "arret de bus", "autocar", "transports en commun", "transport en commun"), 5),
    # Commerces
    Category("supermarche", "Supermarché", "commerce", 6000,
             (F("nwr", ("shop", "~", "supermarket|hypermarket")),),
             ("supermarche", "supermarches", "hypermarche", "grande surface"), 10),
    Category("commerces", "Commerces de proximité", "commerce", 2500,
             (F("nwr", ("shop", "~", "convenience|bakery|butcher|greengrocer|deli|cheese|pastry|newsagent|"
                                     "supermarket|farm|seafood|wine|beverages")),),
             ("commerces", "commerce", "commerces de proximite", "petits commerces", "boutiques", "magasins"), 10),
    Category("boulangerie", "Boulangerie", "commerce", 3000,
             (F("nwr", ("shop", "~", "bakery|pastry")),),
             ("boulangerie", "boulangeries"), 10),
    Category("marche", "Marché", "commerce", 4000,
             (F("nwr", ("amenity", "=", "marketplace")),),
             ("marche", "marche de producteurs"), 10),
    Category("restaurants", "Cafés & restaurants", "commerce", 1500,
             (F("nwr", ("amenity", "~", "restaurant|cafe|bar|pub|fast_food")),),
             ("restaurants", "restaurant", "cafe", "cafes", "bars", "bar"), 10),
    # Services
    Category("ecole", "École", "services", 3000,
             (F("nwr", ("amenity", "~", "school|kindergarten")),),
             ("ecole", "ecoles", "creche", "college", "lycee", "scolaire"), 10),
    Category("pharmacie", "Pharmacie", "services", 4000,
             (F("nwr", ("amenity", "=", "pharmacy")),),
             ("pharmacie",), 10),
    Category("sante", "Médecin / santé", "services", 5000,
             (F("nwr", ("amenity", "~", "doctors|clinic|hospital")), F("nwr", ("healthcare", "~", "doctor|centre"))),
             ("medecin", "medecins", "sante", "maison de sante", "hopital", "docteur"), 10),
    Category("poste", "Bureau de poste", "services", 4000,
             (F("nwr", ("amenity", "=", "post_office")),),
             ("poste", "la poste", "bureau de poste"), 10),
    # Loisirs
    Category("parc", "Parc / jardin public", "loisirs", 1500,
             (F("nwr", ("leisure", "~", "park|garden"), ("access", "!=", "private")),),
             ("parc", "jardin public", "espace vert", "espaces verts"), 10),
    Category("plage", "Plage", "loisirs", 8000,
             (F("nwr", ("natural", "=", "beach")),),
             ("plage", "plages", "mer", "bord de mer"), 15),
    # Nuisances
    Category("autoroute", "Autoroute / voie rapide", "nuisance", 2000,
             (F("way", ("highway", "~", "motorway|trunk")),),
             ("autoroute", "voie rapide", "nationale", "4 voies", "quatre voies"),
             nuisance=True, linear=True),
    Category("voie_ferree", "Voie ferrée", "nuisance", 600,
             (F("way", ("railway", "=", "rail"), ("service", "!=", "yard")),),
             ("voie ferree", "voie de chemin de fer", "rails", "ligne de train"),
             nuisance=True, linear=True),
)

CATEGORIES_BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
CATEGORY_KEYS: tuple[str, ...] = tuple(CATEGORIES_BY_KEY)

GROUP_LABELS = {
    "transport": "Transports",
    "commerce": "Commerces",
    "services": "Services",
    "loisirs": "Loisirs & nature",
    "nuisance": "Nuisances",
}

# Vitesses moyennes (m/min) et facteur de détour (distance réelle / vol d'oiseau).
MODES = {
    "pied": {"label": "à pied", "speed": 75.0, "detour": 1.3},
    "velo": {"label": "à vélo", "speed": 250.0, "detour": 1.3},
    "voiture": {"label": "en voiture", "speed": 600.0, "detour": 1.4},
}


def travel_minutes(distance_m: float, mode: str = "pied") -> float:
    m = MODES.get(mode, MODES["pied"])
    minutes = distance_m * m["detour"] / m["speed"]
    if mode == "voiture":
        minutes += 2  # stationnement, démarrage
    return minutes
