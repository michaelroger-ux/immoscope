"""Appels à Claude simulés : vérifie la forme des requêtes et le traitement des réponses."""
from types import SimpleNamespace

import httpx

from immoscope.models import Listing
from immoscope.search import query_parser
from immoscope.vision import claude_tagger
from immoscope.vision.claude_tagger import analyze_listing, visual_check


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed, self.stop_reason, self.calls = parsed, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason, model=kwargs["model"])


def fake_client(parsed, stop_reason="end_turn"):
    messages = FakeMessages(parsed, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def jpeg_bytes():
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (2000, 1500), (180, 170, 150)).save(buf, format="JPEG")
    return buf.getvalue()


def photo_http():
    data = jpeg_bytes()
    return httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})))


LISTING = Listing(id="t:1", source="t", title="Longère", city="Dinan", url="https://ex.fr/a/1",
                  images=["https://ex.fr/1.jpg", "https://ex.fr/2.jpg", "/local/demo.svg"])


def test_analyze_listing_request_and_result(db):
    db.upsert_listing(LISTING)
    out = claude_tagger._AnalysisOut(
        images=[claude_tagger._ImageOut(index=0, kind="facade", description="Façade en granit"),
                claude_tagger._ImageOut(index=1, kind="jardin_terrain", description="Pelouse et chênes")],
        features=[claude_tagger._FeatureOut(key="facade_pierre", confidence=0.93, image_indices=[0, 7], evidence="Granit"),
                  claude_tagger._FeatureOut(key="piscine", confidence=0.3, image_indices=[1], evidence="?")],
        summary="Longère en granit.")
    client, messages = fake_client(out)
    res = analyze_listing(LISTING, db=db, client=client, http=photo_http())
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "medium"}
    assert call["output_format"] is claude_tagger._AnalysisOut
    images = [b for b in call["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 2 and images[0]["source"]["type"] == "base64"  # l'image locale est ignorée
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert set(res.features) == {"facade_pierre"}  # confiance < 0,5 écartée
    assert res.features["facade_pierre"].image_indices == [0]  # index hors bornes écarté
    assert db.get_vision("t:1").summary == "Longère en granit."
    # même photos : pas de nouvel appel
    analyze_listing(LISTING, db=db, client=client, http=photo_http())
    assert len(messages.calls) == 1


def test_analyze_listing_refusal(db):
    db.upsert_listing(LISTING)
    client, _ = fake_client(None, stop_reason="refusal")
    res = analyze_listing(LISTING, db=db, client=client, http=photo_http())
    assert res.error and "refus" in res.error


def test_visual_check_cached(db):
    db.upsert_listing(LISTING)
    out = claude_tagger._CheckOut(match=True, confidence=0.8, image_indices=[1], evidence="Volets bleus visibles")
    client, messages = fake_client(out)
    r = visual_check(LISTING, "volets bleus", db=db, client=client, http=photo_http())
    assert r["match"] and r["image_indices"] == [1]
    visual_check(LISTING, "volets bleus", db=db, client=client, http=photo_http())
    assert len(messages.calls) == 1


def test_parse_with_claude_maps_to_search_query():
    out = query_parser._PQuery(
        property_types=["maison"], price_min=None, price_max=350000, surface_min=None, surface_max=None,
        land_min=2000, land_max=None, rooms_min=None, bedrooms_min=3,
        area=query_parser._PArea(near="Rennes", radius_km=20, departments=[]),
        visual=[query_parser._PVisual(feature="facade_pierre", required=False, exclude=False)],
        visual_free=[query_parser._PFree(question="volets bleus", required=False)],
        proximity=[query_parser._PProx(category="gare", max_minutes=15, mode="pied", max_meters=None,
                                       min_meters=None, required=True)],
        keywords=[], sort="score")
    client, messages = fake_client(out)
    q = query_parser.parse_with_claude("longère en pierre aux volets bleus…", client=client)
    assert q.price_max == 350000 and q.area.near == "Rennes"
    assert q.visual_free[0].question == "volets bleus"
    assert q.proximity[0].required
    assert messages.calls[0]["output_config"] == {"effort": "low"}
