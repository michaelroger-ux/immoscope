import httpx

from immoscope.location.categories import travel_minutes
from immoscope.location.enrich import enrich_listing
from immoscope.location.geo import distance_to_polyline_m, haversine_m
from immoscope.location.overpass import build_query, summarize
from immoscope.models import Listing

LAT, LON = 48.1000, -1.6800
DLAT_100M = 100 / 111_320


def test_build_query_has_radii_and_line_geometry():
    q = build_query(LAT, LON)
    assert '["shop"~"^(supermarket|hypermarket)$"](around:6000,48.100000,-1.680000)' in q
    assert '.pois out center tags qt;' in q
    assert '.lines out geom tags qt;' in q
    assert 'way["highway"~"^(motorway|trunk)$"](around:2000' in q


def test_distances():
    assert abs(haversine_m(LAT, LON, LAT + DLAT_100M * 10, LON) - 1000) < 2
    d, _ = distance_to_polyline_m(LAT, LON, [(LAT + DLAT_100M * 5, LON - 0.05), (LAT + DLAT_100M * 5, LON + 0.05)])
    assert abs(d - 500) < 3
    assert round(travel_minutes(750, "pied")) == 13


def _elements():
    return [
        {"type": "node", "id": 1, "lat": LAT + DLAT_100M * 3, "lon": LON, "tags": {"shop": "bakery", "name": "Au bon pain"}},
        {"type": "node", "id": 2, "lat": LAT + DLAT_100M * 8, "lon": LON, "tags": {"shop": "bakery"}},
        {"type": "way", "id": 3, "center": {"lat": LAT - DLAT_100M * 20, "lon": LON},
         "tags": {"railway": "station", "name": "Gare de Test"}},
        {"type": "node", "id": 4, "lat": LAT, "lon": LON + 0.001, "tags": {"railway": "station", "station": "subway", "name": "Métro"}},
        {"type": "way", "id": 5, "tags": {"highway": "motorway", "ref": "A84"},
         "geometry": [{"lat": LAT + DLAT_100M * 9, "lon": LON - 0.02}, {"lat": LAT + DLAT_100M * 9, "lon": LON + 0.02}]},
    ]


def test_summarize_categories():
    summaries, pois = summarize(LAT, LON, _elements())
    bakery = summaries["boulangerie"]
    assert bakery.found and bakery.name == "Au bon pain" and abs(bakery.distance_m - 300) <= 1
    assert bakery.count_1000 == 2
    assert summaries["commerces"].count_1000 == 2  # une boulangerie est aussi un commerce de proximité
    assert summaries["gare"].name == "Gare de Test"  # le métro n'est pas compté comme une gare
    assert summaries["metro"].found
    assert summaries["autoroute"].name == "A84" and abs(summaries["autoroute"].distance_m - 900) <= 2
    assert not summaries["plage"].found
    assert any(p["category"] == "boulangerie" for p in pois)


def test_enrich_listing_with_mocked_services(db):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.host)
        if "geopf" in request.url.host:
            return httpx.Response(200, json={"features": [{"geometry": {"coordinates": [LON, LAT]},
                                                           "properties": {"score": 0.9, "type": "municipality", "label": "Testville"}}]})
        return httpx.Response(200, json={"elements": _elements()})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    import immoscope.location.geocode as geocode_mod
    original = geocode_mod._http
    geocode_mod._http = lambda: httpx.Client(transport=transport)
    try:
        listing = Listing(id="t:1", source="t", title="Maison", city="Testville", postcode="35000")
        db.upsert_listing(listing)
        loc = enrich_listing(listing, db=db, client=client)
    finally:
        geocode_mod._http = original
    assert loc.error is None
    assert loc.geo_precision == "commune"  # pas d'adresse : centre de la commune
    assert loc.categories["boulangerie"].found
    assert db.get_location("t:1").categories["gare"].name == "Gare de Test"
    # 2e appel : caches de géocodage et d'Overpass, plus aucun appel réseau
    n = len(calls)
    geocode_mod._http = lambda: httpx.Client(transport=transport)
    try:
        enrich_listing(listing, db=db, client=client, force=True)
    finally:
        geocode_mod._http = original
    assert len(calls) == n
