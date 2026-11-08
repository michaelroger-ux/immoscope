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
_IMAGE_KEYS = ("images", "photos", "pictures", "medias", "imageurls", "image", "photo", "thumbnails", "media",
               "gallery", "galleryimages", "visuals", "visuels", "pictureurls", "photourls", "mainpicture",
               "picture", "cover", "thumbnail", "thumb")
_IMG_URL = re.compile(r"\.(?:jpe?g|png|webp|avif)(?:[?#]|$)|/(?:photos?|images?|img|visuels?|medias?|pictures?)/", re.I)
_NOT_PHOTO = re.compile(r"\.svg(?:[?#]|$)|logo|icon|avatar|sprite|placeholder|marker|pixel", re.I)
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


# Sous-objets qui décrivent autre chose que le bien (agence, contact, annonces similaires…)
_SKIP_KEYS = ("agency", "agence", "agent", "advertiser", "publisher", "seller", "owner", "contact", "user",
              "broker", "company", "professional", "provider", "brand", "logo", "tracking", "analytics", "seo",
              "breadcrumb", "similar", "recommend", "related", "promot", "sponsor", "ads", "banner")


def _skipped(key: str) -> bool:
    return any(s in key for s in _SKIP_KEYS)


def _flat(obj: dict, depth: int = 3) -> dict:
    """Aplatit l'objet : clés de premier niveau prioritaires, puis sous-objets connus (lieu, offre…),
    puis les autres sous-objets (« mainDescription », « hardFacts »…), sauf agence, contact, etc."""
    flat = _lower_keys(obj)
    if depth <= 0:
        return flat
    children = list(flat.items())
    for nk in _NEST_KEYS:
        sub = flat.get(nk)
        if isinstance(sub, list) and sub and all(isinstance(i, dict) for i in sub):
            attrs = _attributes_as_dict(sub)
            sub = attrs if attrs else sub[0]
        if isinstance(sub, dict):
            for k, v in _flat(sub, depth - 1).items():
                flat.setdefault(k, v)
                flat.setdefault(f"{nk}.{k}", v)
    for key, sub in children:
        if key in _NEST_KEYS or not isinstance(sub, dict) or _skipped(key):
            continue
        for k, v in _flat(sub, depth - 1).items():
            flat.setdefault(k, v)
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
    return [u for u in urls if not _NOT_PHOTO.search(u)]


def _fill_template(url: str) -> Optional[str]:
    """Adresses d'images à gabarit (« …/{width}/… ») : on fixe la taille, sinon on renonce."""
    sizes = {"width": "800", "w": "800", "height": "600", "h": "600", "size": "800"}
    out = re.sub(r"\{(\w+)\}", lambda m: sizes.get(m.group(1).lower(), "\x00"), url)
    return None if "\x00" in out else out


def _scan_images(obj: Any, base_url: Optional[str], depth: int = 0, found: Optional[list] = None) -> list[str]:
    """Toutes les adresses de photos présentes dans l'objet (hors agence, logos…)."""
    found = [] if found is None else found
    if depth > 6 or len(found) >= 40:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not (isinstance(k, str) and _skipped(k.lower())):
                _scan_images(v, base_url, depth + 1, found)
    elif isinstance(obj, list):
        for v in obj[:60]:
            _scan_images(v, base_url, depth + 1, found)
    elif isinstance(obj, str) and len(obj) < 600 and (obj.startswith(("http://", "https://", "//")) or obj.startswith("/")):
        url = "https:" + obj if obj.startswith("//") else urljoin(base_url or "", obj)
        if "{" in url:
            url = _fill_template(url) or ""
        if url.startswith("http") and _IMG_URL.search(url) and not _NOT_PHOTO.search(url) and url not in found:
            found.append(url)
    return found


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
    images = _images(flat, url) or _scan_images(obj, url)
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


_ASSIGN = re.compile(r"(?:window\.)?__[A-Z0-9_]+__\s*=\s*|window\.[A-Za-z_]*(?:state|data|props)[A-Za-z_]*\s*=\s*"
                     r"|window\s*\[\s*[\"'][^\"']+[\"']\s*\]\s*=\s*", re.I)
_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _js_string(src: str) -> Optional[str]:
    """Lit un littéral de chaîne JavaScript ('…' ou "…", échappements \\x et \\u compris)."""
    if not src or src[0] not in "\"'":
        return None
    quote, out, i = src[0], [], 1
    while i < len(src):
        c = src[i]
        if c == quote:
            return "".join(out)
        if c == "\\" and i + 1 < len(src):
            n = src[i + 1]
            if n == "x" and i + 3 < len(src):
                out.append(chr(int(src[i + 2:i + 4], 16)))
                i += 4
                continue
            if n == "u" and i + 5 < len(src):
                out.append(chr(int(src[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_JS_ESCAPES.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return None


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
                try:
                    text = _js_string(rest[len("JSON.parse("):].lstrip())
                    if text:
                        blobs.append(json.loads(text))
                except (json.JSONDecodeError, ValueError, TypeError):
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


def _merge_cards(listings: list[Listing], cards: list[Listing], page_url: Optional[str]) -> list[Listing]:
    """Associe chaque annonce à sa carte (même identifiant, même lien ou même prix) et comble les manques."""
    if not cards:
        return listings
    by_id = {c.id.split(":", 1)[1].lower(): c for c in cards}
    by_url = {c.url: c for c in cards if c.url}
    prices: dict[float, list[Listing]] = {}
    for c in cards:
        if c.price:
            prices.setdefault(c.price, []).append(c)
    out = []
    for item in listings:
        ext = item.id.split(":", 1)[1].lower()
        card = by_id.get(ext) or (by_url.get(item.url) if item.url != page_url else None) \
            or next((c for c in cards if ext and ext in (c.url or "").lower()), None) \
            or (prices[item.price][0] if item.price in prices and len(prices[item.price]) == 1 else None)
        if card is None:
            out.append(item)
            continue
        merged = _merge(item, card)
        if (not item.url or item.url == page_url) and card.url:
            merged = merged.model_copy(update={"url": card.url})
        # la description propre de l'annonce prime sur le texte brut de la carte
        merged = merged.model_copy(update={"description": item.description or card.description})
        out.append(merged)
    return out


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
    is_ad_page = bool(page_url and _ad_url(page_url, None))
    if len(embedded) >= 2:  # page de résultats : chaque annonce complétée par sa carte affichée
        return embedded if is_ad_page else _merge_cards(embedded, extract_cards(soup, page_url), page_url)
    # page de résultats sans données intégrées exploitables : cartes affichées
    # (sur une page d'annonce, les cartes sont des « annonces similaires » : on les ignore)
    cards = [] if is_ad_page else extract_cards(soup, page_url)
    if len(cards) >= 2:
        return cards
    candidates = embedded + ld
    if candidates:
        main = candidates[0]
        for extra in candidates[1:] + ([og_listing] if og_listing else []):
            main = _merge(main, extra)
        return [main]
    if cards:
        return cards
    if og_listing and is_ad_page:
        # page d'annonce sans données structurées : OpenGraph complété par le texte de la page
        main_text = (soup.find("main") or soup.body or soup).get_text(" ", strip=True)
        facts = {k: v for k, v in text_facts(main_text[:20000]).items() if getattr(og_listing, k) in (None, "")}
        og_listing = og_listing.model_copy(update=facts)
    if og_listing and (og_listing.price or (og_listing.images and _looks_like_property(og_listing))):
        return [og_listing]
    return []


_PROPERTY_WORDS = re.compile(r"\b(maison|appartement|villa|longere|terrain|propriete|immeuble|studio|duplex|loft|"
                             r"mas|chalet|ferme|fermette|bastide|manoir|pieces?|chambres?|m2|vendre|vente|achat|"
                             r"immobili\w*|house|apartment|flat|bedrooms?)\b")


def _looks_like_property(listing: Listing) -> bool:
    """Une page sans prix n'est gardée que si elle parle visiblement d'un bien (pas une vidéo, un article…)."""
    return bool(_PROPERTY_WORDS.search(normalize(f"{listing.title} {listing.description}")))


_WINDOW_ASSIGN = re.compile(r"""window\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*["']([^"']+)["']\s*\])\s*=""")


def describe_page(html: str) -> dict:
    """Empreinte de la structure d'une page (sans son contenu), pour diagnostiquer une page non reconnue."""
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script")
    ld_types = []
    for o in _json_ld(soup):
        t = o.get("@type")
        ld_types += t if isinstance(t, list) else [t]
    return {
        "taille": len(html),
        "titre": (soup.title.get_text(strip=True)[:80] if soup.title else None),
        "scripts": len(scripts),
        "scripts_json": [s.get("id") or s.get("type") for s in scripts
                         if (s.get("type") or "").lower() in ("application/json", "application/ld+json")][:15],
        "window": sorted({a or b for a, b in _WINDOW_ASSIGN.findall(html)})[:25],
        "json_ld": ld_types[:10],
        "next_data": "__NEXT_DATA__" in html,
        "mentions_prix": html.count('"price"') + html.count('\\"price\\"'),
        "liens_annonces": len(re.findall(r'href="[^"]*/annonces/', html)),
    }


# ----------------------------------------------------------------------------------------
# Lecture des « cartes » d'annonces affichées (page rendue envoyée par le marque-page)
# ----------------------------------------------------------------------------------------

_AD_PATH = re.compile(r"(annonce|/ads?/|classified|detail|/achat|/vente|/bien|propert|listing|/vi/|/offre)", re.I)
_LONG_ID = re.compile(r"\d{6,}")
# identifiant de 8 à 40 caractères avec au moins 3 chiffres (« 262U3177U91W »), seul dans un segment d'URL
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?=(?:[A-Za-z]*\d){3})[A-Za-z0-9]{8,40}(?![A-Za-z0-9])")


def _url_id(url: str) -> Optional[str]:
    path = urlparse(url).path
    last = re.sub(r"\.\w{2,5}$", "", path.rstrip("/").rsplit("/", 1)[-1])
    tokens = _ID_TOKEN.findall(last) or _LONG_ID.findall(path) or _LONG_ID.findall(urlparse(url).query)
    return max(tokens, key=len) if tokens else None
_NB = r"\d{1,3}(?:[  \xa0.]\d{3})+|\d+"
_PRICE_TXT = re.compile(rf"({_NB})\s*(?:€|euros?\b)(?!\s*/\s*m)", re.I)
_LAND_TXT = re.compile(rf"terrain\D{{0,25}}?({_NB})\s*m(?:²|2)|({_NB})\s*m(?:²|2)\s*de\s+terrain"
                       r"|(\d+(?:[.,]\d+)?)\s*ha\b", re.I)
_SURF_TXT = re.compile(rf"({_NB}(?:[.,]\d+)?)\s*m(?:²|2)(?!\s*de\s+terrain)", re.I)
_ROOMS_TXT = re.compile(r"(\d+)\s*pi[eè]ces?\b", re.I)
_BEDS_TXT = re.compile(r"(\d+)\s*(?:chambres?\b|ch\.)", re.I)
_CP_CITY_TXT = re.compile(r"\b(\d{5})\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+(?:[ \-](?:[Ll]e|[Ll]a|[Ll]es|sur|sous|en|de|d'|[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+)){0,4})")
_CITY_CP_TXT = re.compile(r"([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+(?:[ \-][A-Za-zÀ-ÿ'’\-]+){0,4})\s*\((\d{5}|\d{2}|2[AB])\)")


def _ad_url(href: str, page_url: Optional[str]) -> Optional[str]:
    full = urljoin(page_url or "", href)
    u = urlparse(full)
    if u.scheme not in ("http", "https") or not _AD_PATH.search(u.path):
        return None
    last = re.sub(r"\.\w{2,5}$", "", u.path.rstrip("/").rsplit("/", 1)[-1])
    if _LONG_ID.search(u.path) or _ID_TOKEN.search(last):
        return u._replace(query="", fragment="").geturl()
    # identifiant passé en paramètre (?id=123456) — mais pas un filtre de recherche comme priceMax=400000
    if re.search(r"(?:^|&)(?:id|ad|adid|ad_id|idannonce|listing|listingid|classified|classifiedid)=\d{6,}", u.query, re.I):
        return u._replace(fragment="").geturl()
    return None


def _img_urls(node, base: Optional[str]) -> list[str]:
    urls: list[str] = []
    for tag in node.find_all(["img", "source"]):
        cands = [tag.get(a) for a in ("src", "data-src", "data-lazy", "data-original")]
        for attr in ("srcset", "data-srcset"):
            if tag.get(attr):
                cands += [part.strip().split(" ")[0] for part in tag[attr].split(",")]
        for c in cands:
            if not c or c.startswith("data:"):
                continue
            full = urljoin(base or "", c.strip())
            low = full.lower()
            if full.startswith("http") and not low.endswith(".svg") and "logo" not in low and "icon" not in low \
                    and full not in urls:
                urls.append(full)
    return urls


def _number(raw: Optional[str]) -> Optional[float]:
    return parse_number(raw) if raw else None


def text_facts(text: str) -> dict:
    """Prix, surfaces, pièces et commune lus dans un texte d'annonce (« 349 000 € · 120 m² · 5 pièces »)."""
    facts: dict = {}
    prices = [v for m in _PRICE_TXT.finditer(text) if (v := _number(m.group(1))) and v >= 10_000]
    if prices:
        facts["price"] = prices[0]
    if m := _LAND_TXT.search(text):
        if m.group(3):
            facts["land_surface"] = (_number(m.group(3)) or 0) * 10_000
        else:
            facts["land_surface"] = _number(m.group(1) or m.group(2))
    land_spans = [m.span() for m in _LAND_TXT.finditer(text)]
    for m in _SURF_TXT.finditer(text):
        if any(a <= m.start() < b for a, b in land_spans):
            continue
        v = _number(m.group(1))
        if v and 8 <= v <= 2000:
            facts["surface"] = v
            break
    if m := _ROOMS_TXT.search(text):
        facts["rooms"] = int(m.group(1))
    if m := _BEDS_TXT.search(text):
        facts["bedrooms"] = int(m.group(1))
    if m := _CP_CITY_TXT.search(text):
        facts["postcode"], facts["city"] = m.group(1), m.group(2).strip()
    elif m := _CITY_CP_TXT.search(text):
        facts["city"] = m.group(1).strip()
        if len(m.group(2)) == 5:
            facts["postcode"] = m.group(2)
    return facts


def extract_cards(soup: BeautifulSoup, page_url: Optional[str]) -> list[Listing]:
    """Annonces lues dans les cartes de résultats : un lien vers l'annonce + un prix dans le même bloc."""
    source = _source_name(page_url)
    page_key = _ad_url(page_url, None) if page_url else None
    anchors: dict[str, list] = {}
    for a in soup.find_all("a", href=True):
        key = _ad_url(a["href"], page_url)
        if key and key != page_key:
            anchors.setdefault(key, []).append(a)
    if not anchors:
        return []
    owner: dict[int, set] = {}  # élément -> annonces qu'il contient

    def ads_in(node) -> set:
        nid = id(node)
        if nid not in owner:
            owner[nid] = {k for a in node.find_all("a", href=True) if (k := _ad_url(a["href"], page_url))}
            if node.name == "a" and (k := _ad_url(node.get("href", ""), page_url)):
                owner[nid].add(k)
        return owner[nid]

    listings: list[Listing] = []
    for url, links in anchors.items():
        card = links[0]
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]"):
            parent = card.parent
            if ads_in(parent) - {url} or len(parent.get_text(" ", strip=True)) > 3000:
                break
            card = parent
        text = card.get_text(" ", strip=True)
        facts = text_facts(text)
        if "price" not in facts:
            continue
        heading = card.find(["h1", "h2", "h3", "h4"])
        title = (heading.get_text(" ", strip=True) if heading else "") or links[0].get("title") \
            or links[0].get("aria-label") or links[0].get_text(" ", strip=True) or text[:90]
        ext_id = _url_id(url) or hashlib.sha1(url.encode()).hexdigest()[:12]
        listings.append(Listing(
            id=f"{source}:{ext_id}", source=source, url=url, title=title[:200], description=text[:1500],
            property_type=_ptype({}, f"{title} {text[:300]}"), images=_img_urls(card, page_url)[:20],
            raw={"card_text": text[:3000]}, **facts,
        ))
    return listings


def _diag_skeleton(obj: Any, depth: int = 0, max_items: int = 60) -> Any:
    """Squelette d'un objet JSON : clés, types et courts extraits (pour les journaux)."""
    if depth > 3:
        return "…"
    if isinstance(obj, dict):
        return {k: _diag_skeleton(v, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [f"{len(obj)} élément(s)", _diag_skeleton(obj[0], depth + 1)] if obj else []
    if isinstance(obj, str):
        return obj[:40]
    return obj


def diagnose_page(html: str, page_url: Optional[str] = None) -> list[str]:
    """Lignes de diagnostic d'une page non reconnue : structure, liens d'annonces, données intégrées."""
    soup = BeautifulSoup(html, "lxml")
    lines = [f"structure {json.dumps(describe_page(html), ensure_ascii=False)}"]
    ad_links = sorted({k for a in soup.find_all("a", href=True) if (k := _ad_url(a["href"], page_url))})
    lines.append(f"liens d'annonces : {len(ad_links)} ; exemples {[urlparse(u).path for u in ad_links[:3]]}")
    body_text = soup.get_text(" ", strip=True)
    snippets = [body_text[max(0, m.start() - 70):m.end() + 50] for m in _PRICE_TXT.finditer(body_text)][:3]
    lines.append(f"textes autour des prix : {json.dumps(snippets, ensure_ascii=False)}")
    price_re = re.compile(r"price|prix", re.I)
    for b, blob in enumerate(_embedded_json(soup)[:6]):
        top = list(blob)[:20] if isinstance(blob, dict) else f"liste de {len(blob)}" if isinstance(blob, list) else type(blob).__name__
        lines.append(f"données intégrées n°{b} : clés {top}")
        patterns: dict[str, list] = {}

        def walk(v: Any, path: str, d: int = 0):
            if d > 30:
                return
            if isinstance(v, dict):
                if any(isinstance(k, str) and price_re.search(k) for k in v):
                    patterns.setdefault(path, []).append(v)
                for k, x in v.items():
                    walk(x, f"{path}.{k}", d + 1)
            elif isinstance(v, list):
                for x in v[:200]:
                    walk(x, f"{path}[]", d + 1)
        walk(blob, "$")
        for path, objs in sorted(patterns.items(), key=lambda kv: -len(kv[1]))[:3]:
            sk = json.dumps(_diag_skeleton(objs[0]), ensure_ascii=False, default=str)
            lines.append(f"  objets avec prix {path} ×{len(objs)} : {sk[:1800]}")
    return lines
