"""Pages de sites récents : données en window["…"] = JSON.parse("…"), et cartes de résultats rendues."""
import json

from immoscope.sources.extract import diagnose_page, extract_listings, text_facts

CARD = """
<li class="card"><a href="/annonces/achat/maison/{slug}/{id}.htm" aria-label="Voir l'annonce">
  <picture><source srcset="https://v.seloger.example/{id}/a.jpg 1x, https://v.seloger.example/{id}/a@2x.jpg 2x">
  <img src="https://v.seloger.example/{id}/a.jpg"></picture></a>
  <div><h2>Maison {pieces} pièces {surface} m²</h2>
  <p>{prix} €</p><p>{prixm2} €/m²</p>
  <ul><li>{pieces} pièces</li><li>{chambres} chambres</li><li>{surface} m²</li><li>Terrain {terrain} m²</li></ul>
  <p>{ville} ({cp})</p></div>
  <a href="/annonces/achat/maison/{slug}/{id}.htm#photos">Photos</a>
  <a href="/agences/agence-du-centre-123456/">Agence du centre</a>
</li>"""


def _search_page():
    cards = "".join(CARD.format(slug=s, id=i, pieces=p, surface=su, prix=pr, prixm2=pm, chambres=ch, terrain=t,
                                ville=v, cp=cp)
                    for s, i, p, su, pr, pm, ch, t, v, cp in [
                        ("dourdan-91", 245117891, 5, 120, "349 000", "2 908", 3, "2 150", "Dourdan", "91410"),
                        ("rambouillet-78", 245117892, 6, 145, "395 000", "2 724", 4, "3 000", "Rambouillet", "78120"),
                    ])
    return f"""<html><head><title>Achat maison Île-de-France</title>
      <meta property="og:title" content="Maisons à vendre"><meta property="og:image" content="https://www.seloger.example/og.png">
      </head><body><header><a href="/annonces/achat/maison/">Toutes les annonces</a></header>
      <main><ul>{cards}</ul></main></body></html>"""


def test_search_page_cards():
    url = "https://www.seloger.com/classified-search?distributionTypes=Buy&estateTypes=House&priceMax=400000"
    listings = extract_listings(_search_page(), url)
    assert [x.id for x in listings] == ["seloger:245117891", "seloger:245117892"]
    a = listings[0]
    assert a.url == "https://www.seloger.com/annonces/achat/maison/dourdan-91/245117891.htm"
    assert (a.price, a.surface, a.land_surface, a.rooms, a.bedrooms) == (349000, 120, 2150, 5, 3)
    assert (a.city, a.postcode, a.property_type) == ("Dourdan", "91410", "maison")
    assert a.images[0] == "https://v.seloger.example/245117891/a.jpg"
    assert "Maison 5 pièces" in a.title


def test_window_bracket_json_parse():
    data = {"app": {"results": {"items": [
        {"id": 245117893, "title": "Longère en pierre", "price": 310000, "url": "/annonces/achat/maison/x/245117893.htm",
         "photos": ["https://img.example/1.jpg"], "city": "Étampes", "zipCode": "91150", "livingArea": 130}]}}}
    escaped = json.dumps(json.dumps(data)).replace("<", "\\x3C")
    html = f'<html><body><script>window["__UFRN_LIFECYCLE_SERVERREQUEST__"]=JSON.parse({escaped});</script></body></html>'
    [x] = extract_listings(html, "https://www.seloger.com/classified-search?x=1")
    assert (x.title, x.price, x.surface, x.postcode) == ("Longère en pierre", 310000, 130, "91150")


def test_text_facts_variants():
    f = text_facts("Maison 7 pièces 180 m² terrain de 1,2 ha 520 000 € 2 889 €/m² Milly-la-Forêt (91490)")
    assert (f["price"], f["surface"], f["land_surface"], f["rooms"]) == (520000, 180, 12000, 7)
    assert (f["city"], f["postcode"]) == ("Milly-la-Forêt", "91490")
    assert text_facts("1 500 m² de terrain, 95 m² habitables")["surface"] == 95


def test_diagnose_lists_structure():
    lines = diagnose_page(_search_page(), "https://www.seloger.com/classified-search?x=1")
    assert lines[0].startswith("structure")
    assert "liens d'annonces : 2" in lines[1]
    assert "349 000 €" in lines[2]


def test_ad_page_ignores_similar_ads():
    similar = "".join(CARD.format(slug="x", id=i, pieces=4, surface=90, prix="250 000", prixm2="2 777", chambres=2,
                                  terrain="800", ville="Arpajon", cp="91290") for i in (245117801, 245117802))
    html = f"""<html><head><meta property="og:title" content="Maison 6 pièces 160 m² Fontainebleau">
      <meta property="og:image" content="https://v.seloger.example/main/1.jpg"></head><body>
      <main><h1>Maison 6 pièces 160 m²</h1><p>398 000 €</p><p>Terrain de 2 400 m²</p><p>Fontainebleau (77300)</p></main>
      <aside><ul>{similar}</ul></aside></body></html>"""
    [x] = extract_listings(html, "https://www.seloger.com/annonces/achat/maison/fontainebleau-77/245117900.htm")
    assert x.title == "Maison 6 pièces 160 m² Fontainebleau"
    assert (x.price, x.surface, x.land_surface, x.postcode) == (398000, 160, 2400, "77300")


def test_search_url_with_price_filter_is_not_an_ad():
    from immoscope.sources.extract import _ad_url
    assert _ad_url("https://www.seloger.com/classified-search?priceMax=400000&plotSpaceMin=2000", None) is None
    assert _ad_url("https://immo.example/annonce?id=1234567", None) == "https://immo.example/annonce?id=1234567"


def test_embedded_objects_completed_by_cards():
    """Données intégrées pauvres (identifiant, titre, prix) + cartes affichées (photos, texte, lien)."""
    items = [{"id": "262U3177U91W", "title": "Maison", "price": {"value": 349000, "currency": "EUR"},
              "mainDescription": {"headline": "Maison", "description": "Belle longère en pierre, jardin arboré, au calme."},
              "hardFacts": {"livingSpace": "120 m²"}, "location": {"city": "Dourdan", "zipCode": "91410"},
              "agency": {"name": "Agence du centre", "logo": "https://img.example/agence/logo.png"}},
             {"id": "26NKNN4LMUV1", "title": "Maison", "price": {"value": 395000},
              "location": {"city": "Rambouillet", "zipCode": "78120"}, "rooms": 6}]
    data = {"search": {"classifieds": items}}
    cards = "".join(f"""<li><a href="/annonces/achat/maison/{v}/{i}.htm"><img src="https://v.seloger.example/{i}/1.jpg"></a>
        <h3>Maison {p} pièces</h3><p>{pr} €</p><p>{v.title()} ({cp})</p></li>"""
                    for i, p, pr, v, cp in [("262U3177U91W", 5, "349 000", "dourdan", "91410"),
                                            ("26NKNN4LMUV1", 6, "395 000", "rambouillet", "78120")])
    html = (f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
            f'<ul>{cards}</ul></body></html>')
    url = "https://www.seloger.com/classified-search?distributionTypes=Buy&priceMax=400000"
    a, b = extract_listings(html, url)
    assert a.id == "seloger:262U3177U91W" and a.price == 349000
    assert a.description.startswith("Belle longère")  # description trouvée dans « mainDescription »
    assert a.images == ["https://v.seloger.example/262U3177U91W/1.jpg"]  # photo de la carte, pas le logo d'agence
    assert a.url == "https://www.seloger.com/annonces/achat/maison/dourdan/262U3177U91W.htm"
    assert b.images and b.url.endswith("26NKNN4LMUV1.htm") and "395 000 €" in b.description


def test_photos_found_under_unusual_keys():
    from immoscope.sources.extract import map_object
    obj = {"id": "ABC12345", "title": "Maison", "price": 300000,
           "media": {"gallery": [{"variants": {"large": "https://cdn.example/visuels/1/large.jpg"}},
                                 {"variants": {"large": "https://cdn.example/visuels/2/large.jpg"}}]},
           "seller": {"avatar": "https://cdn.example/avatars/9.jpg"}}
    x = map_object(obj, "https://immo.example/recherche")
    assert x.images == ["https://cdn.example/visuels/1/large.jpg", "https://cdn.example/visuels/2/large.jpg"]
