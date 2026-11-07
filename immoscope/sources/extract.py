"""Extraction d'annonces depuis le HTML d'une page d'un site d'annonces.

Pas de « scraper » spécifique à un site : on exploite les données structurées que la plupart des sites
embarquent dans leurs pages, dans cet ordre :
1. JSON-LD (schema.org : RealEstateListing, Offer, House, Apartment, Product…) ;
2. JSON embarqué par les frameworks (__NEXT_DATA__ de Next.js, window.__INITIAL_STATE__, etc.),
   parcouru pour y trouver les objets qui ressemblent à une annonce (titre + prix + photos/surface/lieu) ;
3. balises OpenGraph (og:title, og:image…) en dernier recours.
Fonctionne aussi bien sur une page d'annonce que sur une page de résultats (plusieurs annonces).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Listing
from ..textutil import normalize, parse_number

_TITLE_KEYS = ("subject", "title", "titre", "name", "libelle", "heading", "headline")
_DESC_KEYS = ("body", "description", "descriptif", "text", "texte", "longdescription", "annonce")
_PRICE_KEYS = ("price", "prix", "pricevalue", "price_eur", "priceamount", "amount", "pricewithoutfees", "displayedprice")
_ID_KEYS = ("list_id", "listid", "ad_id", "adid", "listingid", "classifiedid", "id", "reference", "ref", "identifier")
_URL_KEYS = ("url", "permalink", "link", "href", "adurl", "classifiedurl", "canonicalurl")
_SURFACE_KEYS = ("square", "surface", "livingarea", "living_area", "surfacearea", "habitablesurface", "floorsize",
                 "surfacehabitable", "area", "size", "m2")
_LAND_KEYS = ("land_plot_surface", "landsurface", "land_surface", "surfaceterrain", "terrain", "landarea",
              "plotarea", "plotsurface", "lotsize", "gardensurface", "surface_terrain")
_ROOMS_KEYS = ("rooms", "nbrooms", "nb_rooms", "roomsquantity", "numberofrooms", "pieces", "nbpieces", "roomcount")
_BEDROOMS_KEYS = ("bedrooms", "nbbedrooms", "nb_bedrooms", "bedroomsquantity", "numberofbedrooms", "chambres",
                  "bedroomcount")
_TYPE_KEYS = ("real_estate_type", "propertytype", "estatetype", "realestatetype", "typebien", "type_bien",
              "property_type", "category_name", "@type")
_CITY_KEYS = ("city", "ville", "addresslocality", "locality", "town", "city_label", "cityname")
_POSTCODE_KEYS = ("zipcode", "postalcode", "postal_code", "codepostal", "zip_code", "zip", "cp")
_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lng", "lon", "longitude", "long")
_ADDRESS_KEYS = ("streetaddress", "address", "adresse", "street")
_DATE_KEYS = ("first_publication_date", "publicationdate", "datepublished", "published_at", "createdat",
              "creationdate", "index_date", "datecreated")
_IMAGE_KEYS = ("images", "photos", "pictures", "medias", "imageurls", "image", "photo", "thumbnails", "media")
_NEST_KEYS = ("location", "address", "geo", "offers", "about", "mainentity", "itemoffered", "attributes",
              "characteristics", "features", "details", "property", "realestate", "floorsize", "position",
              "coordinates", "pricing", "price")

_LD_TYPES = {"realestatelisting", "offer", "house", "apartment", "residence", "singlefamilyresidence",
             "accommodation", "product", "place", "landform", "residentialbuilding", "itempage"}
_IMG_EXT = re.compile(r"\.(jpe?g|png|webp|avif|gif)(\?|$)|/images?/|/photos?/|img|picture|media", re.I)


def _lower_keys(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        if isinstance(k, str):
            out.setdefault(k.lower(), v)
    return out


def _attributes_as_dict(value: Any) -> dict:
    """Listes d'attributs [{key, value}, …] (Leboncoin et d'autres) → dict."""
    out: dict = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                li = _lower_keys(item)
                k = li.get("key") or li.get("name") or li.get("label") or li.get("code")
                v = li.get("value") if li.get("value") not in (None, "") else li.get("values") or li.get("value_label")
                if isinstance(k, str) and v is not None:
                    out[k.lower()] = v
                    if li.get("value_label"):
                        out[k.lower() + "_label"] = li.get("value_label")
    return out


def _flat(obj: dict, depth: int = 2) -> dict:
    """Aplatit l'objet : clés de premier niveau prioritaires, puis sous-objets connus (lieu, offre…)."""
    flat = _lower_keys(obj)
    if depth <= 0:
        return flat
    for nk in _NEST_KEYS:
        sub = flat.get(nk)
        if isinstance(sub, list) and sub and all(isinstance(i, dict) for i in sub):
            attrs = _attributes_as_dict(sub)
            sub = attrs if attrs else sub[0]
        if isinstance(sub, dict):
            for k, v in _flat(sub, depth - 1).items():
                flat.setdefault(k, v)
                flat.setdefault(f"{nk}.{k}", v)
    return flat


def _first(flat: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        v = flat.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list) and v:
        return _num(v[0])
    if isinstance(v, dict):
        lv = _lower_keys(v)
        return _num(_first(lv, ("value", "amount", "price", "min", "valuemin")))
    if isinstance(v, str):
        m = re.search(r"\d[\d\s \xa0.,]*", v)
        if m:
            return parse_number(m.group(0).rstrip(".,"))
    return None


def _text(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, list) and v:
        return _text(v[0])
    if isinstance(v, dict):
        return _text(_first(_lower_keys(v), ("label", "name", "value", "text")))
    return None


def _images(flat: dict, base_url: Optional[str]) -> list[str]:
    urls: list[str] = []

    def add(u: Any):
        if isinstance(u, str) and u.strip():
            u = u.strip()
            if u.startswith("//"):
                u = "https:" + u
            elif base_url and not u.startswith(("http://", "https://", "data:")):
                u = urljoin(base_url, u)
            if u.startswith(("http://", "https://")) and u not in urls:
                urls.append(u)

    def walk(v: Any, depth: int = 0):
        if depth > 3:
            return
        if isinstance(v, str):
            add(v)
        elif isinstance(v, list):
            for i in v:
                walk(i, depth + 1)
        elif isinstance(v, dict):
            lv = _lower_keys(v)
            # Leboncoin : images.urls_large > urls ; ailleurs : url / src / contentUrl
            for k in ("urls_large", "urls", "large", "url", "src", "contenturl", "href", "uri", "original",
                      "fullsize", "big", "medium"):
                if k in lv:
                    walk(lv[k], depth + 1)
                    if urls:
                        break

    for k in _IMAGE_KEYS:
        if k in flat:
            walk(flat[k])
            if urls:
                break
    return urls


def _ptype(flat: dict, title: str) -> Optional[str]:
    raw = " ".join(str(flat.get(k)) for k in (*_TYPE_KEYS, "real_estate_type_label") if flat.get(k) is not None)
    text = normalize(f"{raw} {title}")
    rt = str(flat.get("real_estate_type") or "")
    if rt in ("1", "2", "3"):  # codes Leboncoin
        return {"1": "maison", "2": "appartement", "3": "terrain"}[rt]
    if re.search(r"\b(maison|house|villa|singlefamilyresidence|longere|mas|chalet|ferme|propriete|pavillon)\b", text):
        return "maison"
    if re.search(r"\b(appartement|apartment|flat|studio|duplex|loft)\b", text):
        return "appartement"
    if re.search(r"\b(terrain|land|landform|plot)\b", text):
        return "terrain"
    if re.search(r"\b(immeuble|building)\b", text):
        return "immeuble"
    return None


def _source_name(url: Optional[str]) -> str:
    if not url:
        return "import"
    host = urlparse(url).netloc.lower().split(":")[0]
    host = re.sub(r"^(www|m|mobile)\.", "", host)
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host or "import"


def map_object(obj: dict, page_url: Optional[str] = None, source: Optional[str] = None) -> Optional[Listing]:
    """Convertit un objet JSON « ressemblant à une annonce » en `Listing`."""
    flat = _flat(obj)
    title = _text(_first(flat, _TITLE_KEYS)) or ""
    ad_url = _text(_first(flat, _URL_KEYS))
    if ad_url and page_url:
        ad_url = urljoin(page_url, ad_url)
    if ad_url and not ad_url.startswith("http"):
        ad_url = None
    url = ad_url or page_url
    src = source or _source_name(url)
    ext_id = _text(_first(flat, _ID_KEYS))
    if ext_id and len(ext_id) > 80:
        ext_id = None
    price = _num(_first(flat, _PRICE_KEYS))
    if price is not None and price < 1000:  # loyer, prix au m², frais… : on ignore
        price = None
    lat, lon = _num(_first(flat, _LAT_KEYS)), _num(_first(flat, _LON_KEYS))
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    description = _text(_first(flat, _DESC_KEYS)) or ""
    if description == title:
        description = ""
    images = _images(flat, url)
    surface = _num(_first(flat, _SURFACE_KEYS))
    land = _num(_first(flat, _LAND_KEYS))
    rooms = _num(_first(flat, _ROOMS_KEYS))
    bedrooms = _num(_first(flat, _BEDROOMS_KEYS))
    city = _text(_first(flat, _CITY_KEYS))
    postcode = _text(_first(flat, _POSTCODE_KEYS))
    if postcode and not re.fullmatch(r"\d{4,5}", postcode):
        m = re.search(r"\b\d{5}\b", postcode)
        postcode = m.group(0) if m else None
    address = _first(flat, _ADDRESS_KEYS)
    address = address if isinstance(address, str) else None

    if not title and not description:
        return None
    key = ext_id or hashlib.sha1(f"{url}|{title}|{price}".encode()).hexdigest()[:12]
    return Listing(
        id=f"{src}:{key}",
        source=src,
        url=url,
        title=title[:300],
        description=description[:20000],
        price=price,
        surface=surface if surface and 5 <= surface <= 5000 else None,
        land_surface=land if land and land >= 10 else None,
        rooms=int(rooms) if rooms and rooms < 100 else None,
        bedrooms=int(bedrooms) if bedrooms and bedrooms < 100 else None,
        property_type=_ptype(flat, title),
        address=address,
        city=city,
        postcode=postcode,
        lat=lat,
        lon=lon,
        geo_precision="approchee" if lat is not None else None,
        images=images[:40],
        published_at=_text(_first(flat, _DATE_KEYS)),
        raw=obj,
    )


def _ad_signals(obj: dict) -> int:
    flat = _flat(obj, depth=1)
    has_title = _first(flat, _TITLE_KEYS) is not None and isinstance(_text(_first(flat, _TITLE_KEYS)), str)
    has_price = _num(_first(flat, _PRICE_KEYS)) is not None
    if not (has_title and has_price):
        return 0
    score = 2
    score += any(k in flat for k in _IMAGE_KEYS)
    score += _first(flat, _DESC_KEYS) is not None
    score += _first(flat, _SURFACE_KEYS + _ROOMS_KEYS + _LAND_KEYS) is not None
    score += _first(flat, _CITY_KEYS + _POSTCODE_KEYS + ("location", "address", "geo")) is not None
    score += _first(flat, _URL_KEYS + _ID_KEYS) is not None
    return score


def find_ad_objects(data: Any, min_signals: int = 4, _depth: int = 0) -> list[dict]:
    """Parcourt un arbre JSON et renvoie les objets qui ressemblent à des annonces (sans les imbriquer)."""
    found: list[dict] = []
    if _depth > 25:
        return found
    if isinstance(data, dict):
        if _ad_signals(data) >= min_signals:
            return [data]
        for v in data.values():
            found += find_ad_objects(v, min_signals, _depth + 1)
    elif isinstance(data, list):
        for v in data:
            found += find_ad_objects(v, min_signals, _depth + 1)
    return found


def _json_ld(soup: BeautifulSoup) -> list[dict]:
    objs: list[dict] = []

    def walk(v: Any):
        if isinstance(v, list):
            for i in v:
                walk(i)
        elif isinstance(v, dict):
            if "@graph" in v:
                walk(v["@graph"])
            types = v.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(isinstance(t, str) and t.lower() in _LD_TYPES for t in types):
                objs.append(v)

    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            walk(json.loads(tag.string or tag.get_text() or "null"))
        except (json.JSONDecodeError, TypeError):
            continue
    return objs


_ASSIGN = re.compile(r"(?:window\.)?__[A-Z0-9_]+__\s*=\s*|window\.[A-Za-z_]*(?:state|data|props)[A-Za-z_]*\s*=\s*", re.I)


def _embedded_json(soup: BeautifulSoup) -> list[Any]:
    blobs: list[Any] = []
    decoder = json.JSONDecoder()
    for tag in soup.find_all("script"):
        content = tag.string or tag.get_text() or ""
        if not content.strip():
            continue
        stype = (tag.get("type") or "").lower()
        if "ld+json" in stype:
            continue
        if tag.get("id") == "__NEXT_DATA__" or stype == "application/json":
            try:
                blobs.append(json.loads(content))
            except json.JSONDecodeError:
                pass
            continue
        for m in _ASSIGN.finditer(content):
            rest = content[m.end():].lstrip()
            if rest.startswith("JSON.parse("):
                inner = rest[len("JSON.parse("):].lstrip()
                try:
                    s, _ = decoder.raw_decode(inner)
                    blobs.append(json.loads(s))
                except (json.JSONDecodeError, TypeError):
                    pass
                continue
            try:
                obj, _ = decoder.raw_decode(rest)
                blobs.append(obj)
            except json.JSONDecodeError:
                continue
    return blobs


def _opengraph(soup: BeautifulSoup, page_url: Optional[str]) -> Optional[dict]:
    def meta(*names: str) -> list[str]:
        out = []
        for n in names:
            for tag in soup.find_all("meta", attrs={"property": n}) + soup.find_all("meta", attrs={"name": n}):
                c = (tag.get("content") or "").strip()
                if c and c not in out:
                    out.append(c)
        return out

    title = (meta("og:title", "twitter:title") or [soup.title.get_text(strip=True) if soup.title else ""])[0]
    if not title:
        return None
    return {
        "title": title,
        "description": (meta("og:description", "description", "twitter:description") or [""])[0],
        "images": meta("og:image", "og:image:url", "twitter:image"),
        "price": (meta("product:price:amount", "og:price:amount") or [None])[0],
        "url": (meta("og:url") or [page_url])[0],
    }


def _merge(primary: Listing, other: Listing) -> Listing:
    data = primary.model_dump()
    for k, v in other.model_dump().items():
        if k in ("id", "source", "raw"):
            continue
        if data.get(k) in (None, "", []) and v not in (None, "", []):
            data[k] = v
    if len(other.description) > len(primary.description):
        data["description"] = other.description
    for img in other.images:
        if img not in data["images"]:
            data["images"].append(img)
    return Listing(**data, raw=primary.raw)


def _id_in_url(listing: Listing, page_url: str) -> bool:
    ext = listing.id.split(":", 1)[1]
    return bool(re.fullmatch(r"\d{5,}|[A-Za-z0-9\-]{8,}", ext)) and ext in page_url


def extract_listings(html: str, page_url: Optional[str] = None) -> list[Listing]:
    """Toutes les annonces trouvées dans la page (une seule pour une page d'annonce)."""
    soup = BeautifulSoup(html, "lxml")
    source = _source_name(page_url)

    ld = [m for o in _json_ld(soup) if (m := map_object(o, page_url, source))]
    embedded: list[Listing] = []
    seen: set[str] = set()
    for blob in _embedded_json(soup):
        for obj in find_ad_objects(blob):
            m = map_object(obj, page_url, source)
            if m and m.id not in seen:
                seen.add(m.id)
                embedded.append(m)
    og = _opengraph(soup, page_url)
    og_listing = map_object(og, page_url, source) if og else None

    # Page d'annonce : l'objet dont l'identifiant figure dans l'URL, complété par JSON-LD et OpenGraph.
    if page_url:
        main = next((e for e in embedded if _id_in_url(e, page_url)), None)
        if main:
            for extra in ld + ([og_listing] if og_listing else []):
                main = _merge(main, extra)
            return [main]
    if len(embedded) >= 2:  # page de résultats
        return embedded
    candidates = embedded + ld
    if candidates:
        main = candidates[0]
        for extra in candidates[1:] + ([og_listing] if og_listing else []):
            main = _merge(main, extra)
        return [main]
    return [og_listing] if og_listing and (og_listing.images or og_listing.price) else []
