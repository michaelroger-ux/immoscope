import json

from immoscope.sources.extract import extract_listings
from immoscope.sources.importers import import_html, import_records


def _lbc_ad(list_id, subject, price, city="Vannes", zipcode="56000"):
    return {
        "list_id": list_id,
        "subject": subject,
        "body": "Belle maison en pierre avec jardin arboré. Proche commerces.",
        "price": [price],
        "url": f"https://www.leboncoin.fr/ad/ventes_immobilieres/{list_id}",
        "first_publication_date": "2026-10-30 10:12:00",
        "images": {"urls_large": [f"https://img.leboncoin.fr/api/v1/lbcpb1/images/{list_id}-1.jpg",
                                  f"https://img.leboncoin.fr/api/v1/lbcpb1/images/{list_id}-2.jpg"]},
        "location": {"city": city, "zipcode": zipcode, "lat": 47.66, "lng": -2.75},
        "attributes": [
            {"key": "real_estate_type", "value": "1", "value_label": "Maison"},
            {"key": "square", "value": "120", "value_label": "120 m²"},
            {"key": "rooms", "value": "5"},
            {"key": "land_plot_surface", "value": "1500"},
        ],
    }


def _next_page(page_props):
    data = {"props": {"pageProps": page_props}}
    return f'<html><head><title>x</title></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def test_next_data_ad_page():
    html = _next_page({"ad": _lbc_ad(2987654321, "Maison en pierre 5 pièces", 289000),
                       "similar": [_lbc_ad(1111111111, "Autre maison", 199000)]})
    url = "https://www.leboncoin.fr/ad/ventes_immobilieres/2987654321"
    [listing] = extract_listings(html, url)
    assert listing.id == "leboncoin:2987654321"
    assert listing.title == "Maison en pierre 5 pièces"
    assert listing.price == 289000
    assert listing.surface == 120 and listing.rooms == 5 and listing.land_surface == 1500
    assert listing.property_type == "maison"
    assert (listing.city, listing.postcode) == ("Vannes", "56000")
    assert listing.lat == 47.66 and listing.lon == -2.75
    assert len(listing.images) == 2 and listing.images[0].endswith("-1.jpg")


def test_next_data_search_page_returns_all_ads():
    html = _next_page({"searchData": {"ads": [_lbc_ad(100000 + i, f"Maison {i}", 150000 + i * 1000) for i in range(3)]}})
    listings = extract_listings(html, "https://www.leboncoin.fr/recherche?category=9")
    assert len(listings) == 3
    assert {x.price for x in listings} == {150000, 151000, 152000}


def test_json_ld_real_estate_listing():
    ld = {
        "@context": "https://schema.org", "@type": "RealEstateListing",
        "name": "Longère à vendre", "description": "Longère en granit, 4 chambres.",
        "url": "https://agence.example/annonces/longere-42",
        "image": ["https://agence.example/p/1.jpg", {"@type": "ImageObject", "url": "https://agence.example/p/2.jpg"}],
        "offers": {"@type": "Offer", "price": "315000", "priceCurrency": "EUR"},
        "about": {"@type": "SingleFamilyResidence", "numberOfRooms": 6,
                  "floorSize": {"@type": "QuantitativeValue", "value": 150, "unitCode": "MTK"},
                  "address": {"@type": "PostalAddress", "addressLocality": "Dinan", "postalCode": "22100"},
                  "geo": {"@type": "GeoCoordinates", "latitude": 48.45, "longitude": -2.05}},
    }
    html = f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'
    [x] = extract_listings(html, "https://agence.example/annonces/longere-42")
    assert x.title == "Longère à vendre" and x.price == 315000
    assert x.surface == 150 and x.rooms == 6
    assert (x.city, x.postcode, x.lat, x.lon) == ("Dinan", "22100", 48.45, -2.05)
    assert x.images == ["https://agence.example/p/1.jpg", "https://agence.example/p/2.jpg"]
    assert x.property_type == "maison"
    assert x.source == "agence"


def test_opengraph_fallback():
    html = """<html><head><meta property="og:title" content="Maison 4 pièces Quimper">
      <meta property="og:description" content="Vue dégagée, terrain plat.">
      <meta property="og:image" content="//cdn.example.fr/a.jpg">
      <meta property="product:price:amount" content="245000"></head><body></body></html>"""
    [x] = extract_listings(html, "https://immo.example.fr/annonce/987654")
    assert x.title == "Maison 4 pièces Quimper" and x.price == 245000
    assert x.images == ["https://cdn.example.fr/a.jpg"]


def test_import_html_and_records(db):
    html = _next_page({"ad": _lbc_ad(2987654321, "Maison en pierre", 289000)})
    r = import_html(html, "https://www.leboncoin.fr/ad/ventes_immobilieres/2987654321", db)
    assert r == {"found": 1, "new": 1, "updated": 0, "ids": ["leboncoin:2987654321"]}
    r2 = import_html(html, "https://www.leboncoin.fr/ad/ventes_immobilieres/2987654321", db)
    assert r2["new"] == 0 and r2["updated"] == 1

    csv_text = "title;url;price;surface;city;postcode;images\nMaison test;https://ex.fr/a/1;200000;90;Rennes;35000;https://ex.fr/1.jpg|https://ex.fr/2.jpg\n"
    r3 = import_records(csv_text, "annonces.csv", db)
    assert r3["found"] == 1
    listing = db.get_listing(r3["ids"][0])
    assert listing.price == 200000 and listing.images == ["https://ex.fr/1.jpg", "https://ex.fr/2.jpg"]
