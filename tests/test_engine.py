from immoscope.models import ProximityCriterion, SearchQuery, VisualCriterion
from immoscope.search.engine import search
from immoscope.search.query_parser import parse_rules
from immoscope.sources.demo import load_demo


def _titles(res):
    return [r["listing"]["title"] for r in res["results"]]


def test_stone_and_trees_ranking(db):
    load_demo(db)
    res = search(parse_rules("maison en pierre avec terrain arboré, gare à moins de 20 min à pied"), db=db)
    assert _titles(res)[0] == "Longère restaurée avec terrain arboré"
    top = res["results"][0]
    assert top["best_image"] == 0  # la photo de façade est mise en avant
    statuses = {c["key"]: c["status"] for c in top["criteria"]}
    assert statuses == {"facade_pierre": "ok", "terrain_arbore": "ok", "gare": "partiel"}


def test_strict_mode_and_required(db):
    load_demo(db)
    q = SearchQuery(visual=[VisualCriterion(feature="vue_mer")],
                    proximity=[ProximityCriterion(category="commerces", max_minutes=10)])
    strict = search(q, db=db, strict=True)
    assert _titles(strict) == ["Maison de pêcheur vue mer"]
    q.visual[0].required = True
    assert search(q, db=db)["matched"] == 1


def test_exclusion_and_nuisance(db):
    load_demo(db)
    q = SearchQuery(visual=[VisualCriterion(feature="vis_a_vis", exclude=True)],
                    proximity=[ProximityCriterion(category="voie_ferree", min_meters=400)])
    res = search(q, db=db)
    roubaix = next(r for r in res["results"] if "Roubaix" in (r["listing"]["city"] or ""))
    assert {c["key"]: c["status"] for c in roubaix["criteria"]} == {"vis_a_vis": "non", "voie_ferree": "partiel"}
    assert res["results"][-1]["listing"]["city"] == "Roubaix"


def test_hard_filters_and_departments(db):
    load_demo(db)
    res = search(parse_rules("maison moins de 350 000 € en Bretagne"), db=db)
    assert set(_titles(res)) == {"Longère restaurée avec terrain arboré", "Maison enduite années 80, quartier calme"}
    res = search(SearchQuery(property_types=["appartement"]), db=db)
    assert _titles(res) == ["Appartement ancien parquet et cheminées, métro Foch"]


def test_area_with_known_center(db):
    load_demo(db)
    q = SearchQuery()
    q.area.lat, q.area.lon, q.area.radius_km = 48.11, -1.68, 25  # Rennes
    res = search(q, db=db)
    assert _titles(res) == ["Maison contemporaine à ossature bois, gare à pied"]
    assert res["results"][0]["distance_km"] < 15


def test_text_mention_used_when_photos_not_analyzed(db):
    from immoscope.models import Listing
    db.upsert_listing(Listing(id="x:1", source="x", title="Maison", description="Jolie maison en pierre avec piscine.",
                              images=["https://ex.fr/1.jpg"]))
    res = search(SearchQuery(visual=[VisualCriterion(feature="facade_pierre"), VisualCriterion(feature="vue_mer")]), db=db)
    crit = {c["key"]: c for c in res["results"][0]["criteria"]}
    assert crit["facade_pierre"]["status"] == "ok" and "mentionné" in crit["facade_pierre"]["detail"]
    assert crit["vue_mer"]["status"] == "inconnu"
    assert res["not_analyzed"] == 1


def test_result_carries_position_from_location_analysis(db):
    from immoscope.models import Listing, LocationAnalysis
    db.upsert_listing(Listing(id="s:1", source="s", title="Maison", city="Dourdan", postcode="91410"))
    db.upsert_listing(Listing(id="s:2", source="s", title="Maison 2", city="Étampes"))
    db.save_location(LocationAnalysis(listing_id="s:1", lat=48.53, lon=2.01, geo_precision="commune"), "48.53,2.01")
    res = {r["listing"]["id"]: r for r in search(SearchQuery(), db=db)["results"]}
    assert (res["s:1"]["lat"], res["s:1"]["lon"], res["s:1"]["geo_precision"]) == (48.53, 2.01, "commune")
    assert res["s:2"]["lat"] is None  # pas encore analysée : pas de position
