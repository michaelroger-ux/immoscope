"""Jeu de démonstration : 12 annonces FICTIVES, illustrées par des dessins générés,
avec des analyses photo et localisation précalculées. Sert à essayer l'interface sans clé API ni réseau.
"""
from __future__ import annotations

import math
import random

from ..db import Database, get_db, now_iso
from ..location.categories import CATEGORIES
from ..models import FeatureHit, ImageNote, Listing, LocationAnalysis, PoiSummary, VisionAnalysis
from ..vision.images import images_hash

SOURCE = "demo"

# (clé, titre, description, prix, surface, terrain, pièces, chambres, type, ville, CP, lat, lon,
#  dessin, caractéristiques {clé: (confiance, photo, justification)}, points d'intérêt {cat: (m, nom, n<1km)})
_DATA = [
    ("dinan-longere", "Longère restaurée avec terrain arboré",
     "À 5 minutes du centre de Dinan, belle longère entièrement rénovée : séjour avec cheminée, poutres, "
     "4 chambres. Terrain de 3200 m² planté d'arbres, au calme.",
     345000, 140, 3200, 6, 4, "maison", "Dinan", "22100", 48.4552, -2.0712,
     dict(facade="pierre", roof="ardoise", scene="campagne", trees=5, beams=True, fireplace=True),
     {"facade_pierre": (0.95, 0, "Moellons de granit apparents sur toute la façade"),
      "toit_ardoise": (0.92, 0, "Couverture en ardoise"), "style_ancien": (0.9, 0, "Longère traditionnelle"),
      "terrain_arbore": (0.9, 1, "Une dizaine d'arbres feuillus autour de la pelouse"),
      "terrain_plat": (0.8, 1, "Terrain plat"), "pelouse": (0.85, 1, "Grande pelouse entretenue"),
      "campagne": (0.8, 1, "Champs visibles en arrière-plan"), "poutres": (0.9, 2, "Poutres apparentes au séjour"),
      "cheminee": (0.9, 2, "Cheminée en pierre"), "renove": (0.8, 2, "Intérieur refait")},
     {"gare": (1800, "Gare de Dinan", 0), "bus": (350, "Les Fontaines", 2), "boulangerie": (900, "Boulangerie du Jerzual", 1),
      "commerces": (850, "Épicerie du Port", 3), "supermarche": (2100, "Super U", 0), "ecole": (1100, "École Sainte-Marie", 0),
      "pharmacie": (1200, "Pharmacie du Château", 0), "sante": (1500, "Maison de santé", 0), "poste": (1600, "La Poste", 0),
      "restaurants": (800, "Crêperie Ahna", 2), "parc": (1300, "Jardin anglais", 0), "marche": (1700, "Marché de Dinan", 0)}),
    ("locmariaquer-peche", "Maison de pêcheur vue mer",
     "Rare : maison de pêcheur à 300 m de la plage, terrasse face au golfe. Séjour lumineux, 2 chambres, "
     "jardinet de 600 m².",
     520000, 95, 600, 4, 2, "maison", "Locmariaquer", "56740", 47.5712, -2.9431,
     dict(facade="pierre", roof="ardoise", scene="mer", trees=1, terrace=True),
     {"facade_pierre": (0.9, 0, "Façade en pierres apparentes, encadrements en granit"),
      "toit_ardoise": (0.9, 0, "Toit d'ardoise"), "vue_mer": (0.95, 1, "La mer occupe l'arrière-plan depuis la terrasse"),
      "vue_degagee": (0.9, 1, "Horizon dégagé sur le golfe"), "terrasse": (0.85, 1, "Terrasse dallée"),
      "style_ancien": (0.75, 0, "Petite maison de pêcheur ancienne"), "lumineux": (0.7, 2, "Grande fenêtre au séjour")},
     {"plage": (300, "Plage de Kerpenhir", 1), "bus": (200, "Le Bourg", 2), "boulangerie": (350, "Boulangerie du Bourg", 1),
      "commerces": (400, "Proxi", 3), "supermarche": (3500, "Intermarché", 0), "restaurants": (250, "Le Bistrot du Port", 5),
      "ecole": (500, "École publique", 1), "pharmacie": (450, "Pharmacie de Locmariaquer", 1), "poste": (420, "La Poste", 1)}),
    ("bruz-contemporaine", "Maison contemporaine à ossature bois, gare à pied",
     "Maison d'architecte récente, bardage bois et grandes baies vitrées, très lumineuse. Jardin clos sans vis-à-vis "
     "de 700 m². Gare TER à pied (Rennes en 10 min), écoles et commerces proches.",
     410000, 130, 700, 5, 4, "maison", "Bruz", "35170", 48.0246, -1.7458,
     dict(facade="bois", roof="plat", scene="ville", trees=2, hedges=True),
     {"facade_bois": (0.95, 0, "Bardage bois vertical"), "style_contemporain": (0.95, 0, "Volumes cubiques, toit plat"),
      "lumineux": (0.9, 2, "Baies vitrées toute hauteur"), "pelouse": (0.8, 1, "Pelouse"),
      "haies_intimite": (0.85, 1, "Haies hautes sur tout le pourtour"), "terrain_plat": (0.85, 1, "Terrain plat")},
     {"gare": (650, "Gare de Bruz", 0), "bus": (200, "Pavail", 3), "commerces": (500, "Centre commercial Bruz", 6),
      "boulangerie": (450, "Le Fournil de Bruz", 2), "supermarche": (900, "Carrefour Market", 1), "ecole": (400, "École Jacques Prévert", 2),
      "pharmacie": (550, "Pharmacie de la Gare", 1), "sante": (700, "Pôle santé", 1), "parc": (600, "Parc de Bruz", 1),
      "restaurants": (480, "Le Pavail", 4), "poste": (800, "La Poste", 1), "autoroute": (1200, "N137", 0),
      "voie_ferree": (450, "Ligne Rennes–Redon", 0)}),
    ("uzes-mas", "Mas en pierre avec piscine et vignes",
     "Authentique mas provençal sur 4500 m² en restanques, piscine, vue sur les vignes. Cuisine d'été, 5 chambres.",
     685000, 180, 4500, 7, 5, "maison", "Uzès", "30700", 44.0275, 4.3889,
     dict(facade="pierre", roof="tuiles", scene="vignes", trees=3, pool=True, slope=True, stone_light=True),
     {"facade_pierre": (0.95, 0, "Pierre calcaire blonde apparente"), "toit_tuiles": (0.95, 0, "Tuiles canal"),
      "style_ancien": (0.9, 0, "Mas traditionnel"), "piscine": (0.95, 1, "Piscine enterrée devant la maison"),
      "vignes": (0.9, 1, "Rangs de vignes au second plan"), "terrain_pente": (0.75, 1, "Terrain en restanques"),
      "campagne": (0.85, 1, "Cadre rural"), "vue_degagee": (0.7, 1, "Vue dégagée sur la vallée")},
     {"boulangerie": (1900, "Boulangerie de Saint-Quentin", 0), "commerces": (1700, "Vival", 0), "supermarche": (2800, "Super U Uzès", 0),
      "bus": (900, "La Bégude", 0), "ecole": (2000, "École de Saint-Quentin", 0), "pharmacie": (2600, "Pharmacie Saint-Quentin", 0),
      "sante": (3200, "Cabinet médical", 0), "marche": (3400, "Marché d'Uzès", 0), "restaurants": (1600, "La Table du Mas", 0)}),
    ("gordes-bastide", "Bastide à rénover, vue sur le Luberon",
     "Bastide ancienne à restaurer entièrement, sur 8000 m² d'oliviers en pente douce. Vue exceptionnelle.",
     390000, 150, 8000, 6, 4, "maison", "Gordes", "84220", 43.9035, 5.1908,
     dict(facade="pierre", roof="tuiles", scene="montagne", trees=4, slope=True, ruin=True, stone_light=True),
     {"facade_pierre": (0.9, 0, "Pierre sèche, enduits tombés"), "a_renover": (0.95, 0, "Volets cassés, toiture abîmée"),
      "style_ancien": (0.9, 0, "Bastide ancienne"), "vue_degagee": (0.9, 1, "Panorama sur la vallée"),
      "vue_montagne": (0.8, 1, "Massif du Luberon à l'horizon"), "terrain_pente": (0.85, 1, "Terrain en pente"),
      "terrain_arbore": (0.85, 1, "Oliviers"), "grand_terrain": (0.8, 1, "Grande parcelle")},
     {"boulangerie": (2400, "Boulangerie de Gordes", 0), "commerces": (2300, "Spar Gordes", 0), "supermarche": (5200, "Intermarché", 0),
      "restaurants": (1400, "La Bastide", 0), "pharmacie": (2500, "Pharmacie de Gordes", 0), "ecole": (2400, "École de Gordes", 0)}),
    ("honfleur-colombages", "Maison normande à colombages, verger",
     "Charmante maison normande à colombages, 3 chambres, cheminée. Verger et potager sur 2500 m², en campagne "
     "à 10 min de Honfleur.",
     365000, 120, 2500, 5, 3, "maison", "Équemauville", "14600", 49.3953, 0.2120,
     dict(facade="colombages", roof="tuiles", scene="campagne", trees=4, orchard=True, fireplace=True, beams=True),
     {"colombages": (0.95, 0, "Pans de bois apparents"), "toit_tuiles": (0.8, 0, "Tuiles plates"),
      "style_ancien": (0.9, 0, "Maison normande traditionnelle"), "potager_verger": (0.85, 1, "Pommiers alignés"),
      "terrain_arbore": (0.8, 1, "Arbres fruitiers"), "pelouse": (0.8, 1, "Herbe"), "campagne": (0.85, 1, "Prairies"),
      "cheminee": (0.85, 2, "Cheminée normande"), "poutres": (0.85, 2, "Poutres")},
     {"bus": (500, "Équemauville Mairie", 1), "boulangerie": (1200, "Boulangerie d'Équemauville", 0), "commerces": (1500, "Carrefour Contact", 0),
      "supermarche": (2600, "Leclerc Honfleur", 0), "ecole": (900, "École primaire", 1), "pharmacie": (2200, "Pharmacie", 0),
      "sante": (2900, "Hôpital de Honfleur", 0), "plage": (4200, "Plage du Butin", 0), "marche": (3000, "Marché de Honfleur", 0),
      "autoroute": (1900, "A29", 0)}),
    ("thones-chalet", "Chalet en bois vue montagne",
     "Chalet savoyard en bois, terrasse plein sud face aux Aravis, à la lisière de la forêt. 4 chambres, poêle à bois.",
     595000, 140, 1200, 6, 4, "maison", "Thônes", "74230", 45.8706, 6.3361,
     dict(facade="bois", roof="ardoise", scene="montagne", trees=6, slope=True, terrace=True, conifers=True),
     {"facade_bois": (0.95, 0, "Chalet en madriers"), "vue_montagne": (0.95, 1, "Sommets enneigés"),
      "foret": (0.9, 1, "Forêt de sapins en lisière"), "terrain_pente": (0.85, 1, "Terrain pentu"),
      "terrasse": (0.85, 1, "Terrasse en bois"), "vue_degagee": (0.85, 1, "Vue dégagée"), "cheminee": (0.75, 2, "Poêle")},
     {"bus": (600, "Les Clefs", 0), "boulangerie": (1300, "Boulangerie Thônes", 0), "commerces": (1250, "Petit Casino", 0),
      "supermarche": (1500, "Super U Thônes", 0), "ecole": (1400, "École des Perrasses", 0), "pharmacie": (1350, "Pharmacie du Midi", 0),
      "sante": (1600, "Centre médical", 0), "marche": (1500, "Marché de Thônes", 0), "restaurants": (900, "Le Chalet", 1)}),
    ("lyon-appartement", "Appartement ancien parquet et cheminées, métro Foch",
     "Lyon 6e, appartement bourgeois de 88 m² : parquet, moulures, deux cheminées, très lumineux. Métro à 3 minutes, "
     "tous commerces au pied de l'immeuble, parc de la Tête d'Or à 500 m.",
     435000, 88, None, 4, 2, "appartement", "Lyon", "69006", 45.7691, 4.8399,
     dict(facade="enduit", roof="ardoise", scene="ville", trees=1, building=True, fireplace=True, parquet=True),
     {"urbain": (0.95, 0, "Rue d'immeubles haussmanniens"), "parquet": (0.95, 2, "Parquet en point de Hongrie"),
      "cheminee": (0.9, 2, "Cheminée en marbre"), "lumineux": (0.85, 2, "Hautes fenêtres"),
      "style_ancien": (0.85, 0, "Immeuble XIXe")},
     {"metro": (280, "Foch", 3), "tram": (900, "Charpennes", 1), "bus": (120, "Foch", 9), "gare": (1900, "Gare Part-Dieu", 0),
      "commerces": (100, "Carrefour City", 38), "boulangerie": (150, "Maison Pozzoli", 6), "supermarche": (300, "Monoprix", 3),
      "restaurants": (80, "Café Foch", 60), "ecole": (350, "École Jean Racine", 3), "pharmacie": (200, "Pharmacie Foch", 4),
      "sante": (250, "Cabinet médical", 6), "poste": (450, "La Poste Lyon 6", 1), "parc": (480, "Parc de la Tête d'Or", 2),
      "marche": (600, "Marché Vendôme", 1)}),
    ("roubaix-brique", "Maison de ville en brique avec jardin",
     "Maison 1930 en brique rouge, 4 chambres, jardin de ville. Métro et gare à pied.",
     235000, 115, 150, 6, 4, "maison", "Roubaix", "59100", 50.6902, 3.1812,
     dict(facade="brique", roof="tuiles", scene="ville", trees=1, neighbors=True),
     {"facade_brique": (0.95, 0, "Brique rouge apparente"), "toit_tuiles": (0.8, 0, "Tuiles mécaniques"),
      "urbain": (0.9, 0, "Maisons mitoyennes"), "pelouse": (0.7, 1, "Petit jardin engazonné"),
      "vis_a_vis": (0.85, 1, "Façades voisines visibles depuis le jardin")},
     {"metro": (450, "Eurotéléport", 0), "tram": (800, "Roubaix Eurotéléport", 1), "bus": (150, "Grand Place", 7),
      "gare": (1600, "Gare de Roubaix", 0), "commerces": (300, "Proxi", 12), "boulangerie": (250, "Paul", 5),
      "supermarche": (700, "Lidl", 1), "ecole": (400, "École Jules Guesde", 3), "pharmacie": (350, "Pharmacie Centrale", 3),
      "sante": (500, "Cabinet médical", 2), "parc": (900, "Parc Barbieux", 1), "restaurants": (200, "Brasserie", 15),
      "voie_ferree": (350, "Ligne Lille–Tourcoing", 0), "autoroute": (1300, "A22", 0)}),
    ("brantome-fermette", "Fermette en pierre à rénover, étang et bois",
     "Corps de ferme à restaurer sur 2,5 hectares avec étang et bois. Idéal projet de vie à la campagne.",
     189000, 110, 25000, 4, 2, "maison", "Brantôme", "24310", 45.3802, 0.6810,
     dict(facade="pierre", roof="tuiles", scene="foret", trees=6, pond=True, ruin=True, stone_light=True),
     {"facade_pierre": (0.9, 0, "Pierre calcaire"), "a_renover": (0.9, 0, "Toiture et menuiseries à refaire"),
      "style_ancien": (0.9, 0, "Ancien corps de ferme"), "eau_sur_terrain": (0.9, 1, "Étang devant la maison"),
      "foret": (0.85, 1, "Bois en bordure"), "campagne": (0.9, 1, "Prairies"), "grand_terrain": (0.85, 1, "Plusieurs hectares"),
      "terrain_arbore": (0.8, 1, "Nombreux arbres")},
     {"boulangerie": (2800, "Boulangerie de Brantôme", 0), "commerces": (2700, "Vival", 0), "supermarche": (4500, "Carrefour Market", 0),
      "pharmacie": (3000, "Pharmacie de Brantôme", 0), "ecole": (2900, "École de Brantôme", 0), "sante": (3100, "Maison de santé", 0),
      "marche": (3000, "Marché de Brantôme", 0)}),
    ("vannes-enduite", "Maison enduite années 80, quartier calme",
     "Maison des années 80 en bon état, sous-sol complet, 4 chambres. Jardin plat de 650 m². Gare et commerces à "
     "proximité.",
     299000, 105, 650, 5, 4, "maison", "Vannes", "56000", 47.6690, -2.7380,
     dict(facade="enduit", roof="ardoise", scene="ville", trees=2),
     {"facade_enduit": (0.9, 0, "Façade crépie blanche"), "toit_ardoise": (0.85, 0, "Ardoise"),
      "pelouse": (0.8, 1, "Pelouse"), "terrain_plat": (0.85, 1, "Jardin plat"), "renove": (0.6, 2, "Bon état général")},
     {"gare": (1400, "Gare de Vannes", 0), "bus": (250, "Kercado", 3), "commerces": (600, "Centre Kercado", 4),
      "boulangerie": (500, "Boulangerie Kercado", 1), "supermarche": (800, "Leclerc", 1), "ecole": (500, "École Kercado", 1),
      "pharmacie": (650, "Pharmacie Kercado", 1), "sante": (900, "Cabinet médical", 1), "poste": (1100, "La Poste", 0),
      "parc": (700, "Parc du Golfe", 1), "restaurants": (650, "Crêperie", 2), "autoroute": (700, "N165", 0)}),
    ("bayeux-manoir", "Manoir en pierre et son parc arboré",
     "Manoir du XVIIe siècle en pierre de Caen, parc de 1,5 ha planté d'arbres centenaires, dépendances. "
     "Cheminées monumentales, poutres.",
     890000, 320, 15000, 11, 7, "maison", "Vaux-sur-Aure", "14400", 49.2940, -0.6955,
     dict(facade="pierre", roof="ardoise", scene="campagne", trees=8, big=True, stone_light=True, beams=True, fireplace=True),
     {"facade_pierre": (0.95, 0, "Pierre de taille calcaire"), "toit_ardoise": (0.9, 0, "Ardoise"),
      "style_ancien": (0.95, 0, "Manoir ancien"), "grand_terrain": (0.9, 1, "Vaste parc"),
      "terrain_arbore": (0.95, 1, "Arbres centenaires"), "pelouse": (0.85, 1, "Pelouses"), "campagne": (0.85, 1, "Bocage"),
      "cheminee": (0.9, 2, "Cheminée monumentale"), "poutres": (0.85, 2, "Poutres")},
     {"gare": (4200, "Gare de Bayeux", 0), "bus": (800, "Vaux-sur-Aure", 0), "boulangerie": (3000, "Boulangerie de Bayeux", 0),
      "commerces": (2900, "Carrefour City", 0), "supermarche": (3800, "Leclerc Bayeux", 0), "ecole": (2500, "École", 0),
      "pharmacie": (3200, "Pharmacie", 0), "sante": (3600, "Centre hospitalier", 0), "plage": (7800, "Plage d'Arromanches", 0)}),
]


def demo_image_url(key: str, n: int) -> str:
    return f"/api/demo/image/{key}/{n}.svg"


def _offset(lat: float, lon: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    b = math.radians(bearing_deg)
    dlat = distance_m * math.cos(b) / 111_320
    dlon = distance_m * math.sin(b) / (111_320 * math.cos(math.radians(lat)))
    return round(lat + dlat, 6), round(lon + dlon, 6)


def build() -> list[tuple[Listing, VisionAnalysis, LocationAnalysis]]:
    out = []
    kinds = ["facade", "jardin_terrain", "interieur"]
    for (key, title, desc, price, surface, land, rooms, bedrooms, ptype, city, postcode, lat, lon, drawing,
         feats, pois) in _DATA:
        lid = f"{SOURCE}:{key}"
        images = [demo_image_url(key, n) for n in range(3)]
        listing = Listing(id=lid, source=SOURCE, url=None, title=title, description=desc, price=price,
                          surface=surface, land_surface=land, rooms=rooms, bedrooms=bedrooms, property_type=ptype,
                          city=city, postcode=postcode, lat=lat, lon=lon, geo_precision="approchee", images=images)
        features = {k: FeatureHit(key=k, confidence=c, source="demo", image_indices=[i], evidence=e)
                    for k, (c, i, e) in feats.items()}
        notes = [ImageNote(index=i, kind=kinds[i], description=d) for i, d in enumerate([
            "Vue de la façade principale (illustration de démonstration).",
            "Vue du jardin et de l'environnement (illustration de démonstration).",
            "Vue de la pièce principale (illustration de démonstration).",
        ])]
        vision = VisionAnalysis(listing_id=lid, model="démo", analyzed_at=now_iso(), features=features,
                                images=notes, summary=f"{title} — analyse fictive fournie avec la démonstration.")
        rng = random.Random(key)
        summaries, poi_list = {}, []
        for cat in CATEGORIES:
            if cat.key in pois and pois[cat.key][0] <= cat.radius_m:
                dist, name, n1k = pois[cat.key]
                plat, plon = _offset(lat, lon, dist, rng.uniform(0, 360))
                summaries[cat.key] = PoiSummary(category=cat.key, found=True, radius_m=cat.radius_m, distance_m=dist,
                                                name=name, lat=plat, lon=plon, count_500=int(dist <= 500) + max(0, n1k // 2),
                                                count_1000=max(n1k, int(dist <= 1000)), count_radius=max(n1k, 1) + 2)
                poi_list.append({"category": cat.key, "name": name, "lat": plat, "lon": plon, "distance_m": dist})
            else:
                summaries[cat.key] = PoiSummary(category=cat.key, found=False, radius_m=cat.radius_m)
        location = LocationAnalysis(listing_id=lid, enriched_at=now_iso(), lat=lat, lon=lon,
                                    geo_precision="approchee", categories=summaries, pois=poi_list)
        out.append((listing, vision, location))
    return out


def load_demo(db: Database | None = None) -> int:
    db = db or get_db()
    items = build()
    for listing, vision, location in items:
        db.upsert_listing(listing)
        db.save_vision(vision, images_hash([u for u in listing.images if u.startswith("http")]))
        db.save_location(location, f"{listing.lat:.4f},{listing.lon:.4f}")
    return len(items)


def drawing_params(key: str) -> dict | None:
    for row in _DATA:
        if row[0] == key:
            return {**row[13], "title": row[1]}
    return None
