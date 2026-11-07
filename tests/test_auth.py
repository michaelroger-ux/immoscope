import json

import pytest
from fastapi.testclient import TestClient

from immoscope import config
from immoscope.api import app


@pytest.fixture()
def protected(db, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "chamois-42")
    monkeypatch.setattr(config, "SECRET_KEY", "secret-de-test")
    return TestClient(app)


def test_everything_locked_without_session(protected):
    c = protected
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/meta").status_code == 401
    assert c.post("/api/demo/load").status_code == 401
    assert c.get("/healthz").json() == {"ok": True}
    assert c.get("/static/app.js").status_code == 200
    assert "Mot de passe" in c.get("/login").text


def test_login_logout_and_password_change(protected, monkeypatch, db):
    c = protected
    assert c.post("/login", data={"password": "faux"}).status_code == 401
    r = c.post("/login", data={"password": "chamois-42"}, follow_redirects=False)
    assert r.status_code == 303 and "immoscope_session" in r.headers["set-cookie"]
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    meta = c.get("/api/meta").json()
    assert meta["auth"] is True and len(meta["import_token"]) == 32 and meta["storage"] == db.kind
    # changer le mot de passe invalide les sessions existantes
    monkeypatch.setattr(config, "PASSWORD", "nouveau")
    assert c.get("/api/meta").status_code == 401
    monkeypatch.setattr(config, "PASSWORD", "chamois-42")
    assert c.get("/api/meta").status_code == 200
    c.get("/logout")
    assert c.get("/api/meta").status_code == 401


def test_bookmarklet_requires_personal_token(protected, db):
    c = protected
    ad = {"list_id": 3000000002, "subject": "Maison en pierre", "body": "Jardin", "price": [210000],
          "images": {"urls_large": ["https://img.example/1.jpg"]}, "location": {"city": "Morlaix", "zipcode": "29600"}}
    page = ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"ad": ad}}}) + "</script></body></html>")
    url = "https://www.leboncoin.fr/ad/ventes_immobilieres/3000000002"
    r = c.post("/api/import/bookmarklet", files={"url": (None, url), "html": (None, page), "token": (None, "mauvais")})
    assert "non reconnu" in r.text and db.count() == 0
    c.post("/login", data={"password": "chamois-42"})
    token = c.get("/api/meta").json()["import_token"]
    fresh = TestClient(app)  # le marque-page n'a pas le cookie de session
    r = fresh.post("/api/import/bookmarklet", files={"url": (None, url), "html": (None, page), "token": (None, token)})
    assert "1 annonce(s) importée(s)" in r.text and db.count() == 1


def test_no_cors_headers(db):
    r = TestClient(app).options("/api/search", headers={"Origin": "https://evil.example",
                                                         "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_locked_on_render_without_password(db, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", None)
    monkeypatch.setattr(config, "ON_RENDER", True)
    c = TestClient(app)
    assert c.get("/healthz").json() == {"ok": True}  # le contrôle de santé de Render passe
    assert c.get("/").status_code == 503 and "IMMO_PASSWORD" in c.get("/").text
    assert c.post("/api/demo/load").status_code == 503
    assert c.get("/static/app.js").status_code == 503
