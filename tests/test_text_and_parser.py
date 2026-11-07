from immoscope.search.query_parser import parse_rules
from immoscope.textutil import find_terms, normalize, parse_number


def test_normalize_and_numbers():
    assert normalize("Façade en Pierre — vis-à-vis") == "facade en pierre vis-a-vis"
    assert parse_number("250 000") == 250000
    assert parse_number("250.000") == 250000
    assert parse_number("1,5") == 1.5
    assert parse_number("1 250 000") == 1250000


def test_find_terms_longest_first_and_negation():
    terms = {"haies": ["sans vis-à-vis"], "vis": ["vis-à-vis"], "piscine": ["piscine"]}
    assert set(find_terms("Jardin sans vis-à-vis", terms)) == {"haies"}
    assert "piscine" not in find_terms("pas de piscine", terms)
    assert "piscine" in find_terms("belle piscine chauffée", terms)


def _visual(q):
    return {v.feature: v for v in q.visual}


def _prox(q):
    return {p.category: p for p in q.proximity}


def test_parse_stone_house_near_station():
    q = parse_rules("Longère en pierre avec terrain arboré de 3000 m², gare à 10 min à pied, "
                    "moins de 300 000 € autour de Rennes")
    assert q.property_types == ["maison"]
    assert q.price_max == 300000
    assert q.land_min == 3000 and q.surface_min is None
    assert q.area.near == "Rennes" and q.area.radius_km == 15
    assert {"facade_pierre", "terrain_arbore"} <= set(_visual(q))
    gare = _prox(q)["gare"]
    assert gare.max_minutes == 10 and gare.mode == "pied"


def test_parse_sea_view_is_visual_not_beach_proximity():
    q = parse_rules("maison avec vue mer, proche commerces et écoles, sans vis-à-vis, 4 pièces, budget 450k€ en Bretagne")
    assert "vue_mer" in _visual(q)
    assert "haies_intimite" in _visual(q)
    assert "plage" not in _prox(q)
    assert {"commerces", "ecole"} <= set(_prox(q))
    assert q.price_max == 450000 and q.rooms_min == 4
    assert q.area.departments == ["22", "29", "35", "56"]


def test_parse_ranges_metro_and_apartment():
    q = parse_rules("appartement T3 à Lyon, métro à moins de 500 m, parquet et cheminée, entre 250 000 et 320 000 €")
    assert q.property_types == ["appartement"]
    assert (q.price_min, q.price_max) == (250000, 320000)
    assert q.rooms_min == 3
    assert q.area.near == "Lyon"
    assert _prox(q)["metro"].max_meters == 500
    assert {"parquet", "cheminee"} <= set(_visual(q))


def test_parse_nuisance_required_and_hectares():
    q = parse_rules("mas provençal en pierre, piscine obligatoirement, loin de l'autoroute, 1 ha, "
                    "dans un rayon de 20 km autour d'Uzès")
    assert q.land_min == 10000
    assert q.area.near == "Uzès" and q.area.radius_km == 20
    assert _visual(q)["piscine"].required
    assert _prox(q)["autoroute"].min_meters == 1500


def test_parse_exclusion_driving_and_department():
    q = parse_rules("maison à rénover dans le Finistère, pas de piscine, boulangerie et supermarché à 5 min en voiture, 3 chambres")
    assert q.area.departments == ["29"]
    assert _visual(q)["piscine"].exclude
    assert "a_renover" in _visual(q)
    for cat in ("boulangerie", "supermarche"):
        assert _prox(q)[cat].mode == "voiture" and _prox(q)[cat].max_minutes == 5
    assert q.bedrooms_min == 3


def test_parse_backward_linked_categories():
    q = parse_rules("commerces et école à pied")
    assert {"commerces", "ecole"} <= set(_prox(q))
