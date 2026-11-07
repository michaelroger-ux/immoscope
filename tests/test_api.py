import json

from fastapi.testclient import TestClient

from immoscope.api import app


def test_api_flow(db):
    client = TestClient(app)
    assert client.get("/").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["count"] == 0 and meta["claude"] is False
    assert client.post("/api/demo/load").json() == {"loaded": 12}

    res = client.post("/api/search", json={"text": "maison avec piscine, loin de l'autoroute"}).json()
    assert res["parser"] == "règles"
    assert res["results"][0]["listing"]["title"] == "Mas en pierre avec piscine et vignes"

    lid = res["results"][0]["listing"]["id"]
    detail = client.get(f"/api/listings/{lid}/detail").json()
    assert detail["vision"]["features"]["piscine"]["confidence"] > 0.9
    assert detail["location"]["categories"]["supermarche"]["found"]

    svg = client.get("/api/demo/image/uzes-mas/1.svg")
    assert svg.status_code == 200 and svg.text.startswith("<svg")

    # requête structurée modifiée par l'interface
    query = res["query"]
    query["visual"] = [{"feature": "piscine", "required": True, "exclude": False}]
    res2 = client.post("/api/search", json={"query": query}).json()
    assert res2["matched"] == 1

    # vision demandée sans clé API : refus explicite
    r = client.post("/api/process", json={"vision": True, "location": False}).json()
    assert r["started"] is False


def test_bookmarklet_import(db):
    client = TestClient(app)
    ad = {"list_id": 3000000001, "subject": "Maison en pierre", "body": "Jardin arboré", "price": [210000],
          "images": {"urls_large": ["https://img.example/1.jpg"]}, "location": {"city": "Morlaix", "zipcode": "29600"}}
    page = ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"ad": ad}}}) + "</script></body></html>")
    r = client.post("/api/import/bookmarklet",
                    files={"url": (None, "https://www.leboncoin.fr/ad/ventes_immobilieres/3000000001"),
                           "html": (None, page)})
    assert r.status_code == 200 and "1 annonce(s) importée(s)" in r.text
    assert db.get_listing("leboncoin:3000000001").city == "Morlaix"
    assert client.post("/api/import/html", json={"html": "<html></html>"}).status_code == 422


def test_ephemeral_storage_warning_on_render(db, monkeypatch):
    from immoscope import config
    client = TestClient(app)
    monkeypatch.setattr(config, "ON_RENDER", True)
    monkeypatch.setattr(config, "PASSWORD", "secret")  # sur Render, sans mot de passe tout est verrouillé
    client.post("/login", data={"password": "secret"})
    monkeypatch.setattr(config, "DATA_DIR_FROM_ENV", False)
    assert client.get("/api/meta").json()["ephemeral_storage"] is (db.kind == "sqlite")
    monkeypatch.setattr(config, "DATA_DIR_FROM_ENV", True)  # disque persistant Render (IMMO_DATA_DIR=/data)
    assert client.get("/api/meta").json()["ephemeral_storage"] is False
