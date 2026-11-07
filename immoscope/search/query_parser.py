"""Compréhension d'une recherche en langage naturel → `SearchQuery`.

- `parse_with_claude` : Claude convertit la phrase en critères structurés (le plus robuste) ;
- `parse_rules` : analyseur par règles, gratuit et hors-ligne, utilisé si Claude n'est pas configuré.
"""
from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel

from .. import config
from ..location.categories import CATEGORIES, CATEGORIES_BY_KEY, CATEGORY_KEYS
from ..models import (FreeVisualCriterion, PropertyType, ProximityCriterion, SearchArea, SearchQuery,
                      TravelMode, VisualCriterion)
from ..textutil import find_term_hits, normalize, parse_number, term_pattern
from ..vision.taxonomy import FEATURE_KEYS, FEATURES, FEATURES_BY_KEY
from .geo_fr import DEPARTMENTS, REGIONS

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------------------
# Analyseur par règles
# ----------------------------------------------------------------------------------------

_CITY = r"([A-ZÀÂÉÈÊÎÔÛÇ][\w’'\-]*(?:[ \-](?:sur|sous|en|le|la|les|de|du|des|d'|d’|l'|l’|lès|lez|aux)?[ \-]?[A-ZÀÂÉÈÊÎÔÛÇ][\w’'\-]*)*)"
_AREA_PATTERNS = [
    # « dans un rayon de 20 km autour de Rennes », « à moins de 30 km de Lyon », « à 15 km de Nantes »
    (re.compile(r"(?:dans un rayon de|rayon de|à moins de|a moins de|moins de|à|a)\s*(\d+)\s*km\s*(?:autour\s+)?(?:de|d'|d’|du)\s*" + _CITY), "radius_city"),
    # « autour de Rennes (20 km) », « près de Vannes », « secteur Dinan »
    (re.compile(r"(?:autour de|autour d'|autour d’|près de|pres de|proche de|aux alentours de|aux environs de|"
                r"environs de|secteur|vers|côté|cote|région de|region de)\s+" + _CITY + r"(?:\s*\(?\s*(\d+)\s*km\)?)?"),
     "city_radius"),
    # « à Nantes », « sur Bordeaux »
    (re.compile(r"(?:^|\s)(?:à|a|sur|dans)\s+" + _CITY), "city"),
]
_AREA_PREPOSITIONS = r"(?:en|dans le|dans la|dans les|dans l|au|aux|sur la|sur le|departement|departement du|" \
                     r"departement de la|departement de l|departement des|du)"

_BOUNDARY = re.compile(r"[.;!?]|\bavec\b|\bmais\b|\bdans\b|\bpour\b")
_CONNECTOR = re.compile(r"^[\s,]*(?:(?:et|ou|,)\s*)?(?:(?:de|d|du|des|la|le|les|l|aux|au|a|une|un)\s+)*[\s,]*$")
_CUE_BEFORE = re.compile(r"(proche|pres|proximite|a cote|a deux pas|a pied|acces|loin|eloigne|a l ecart|"
                         r"\bmin\b|minutes|\bkm\b|\d+\s*m\b|desservi|non loin|tout pres|a moins)")
_CUE_AFTER = re.compile(r"^\s*(a pied|a velo|en velo|en voiture|a proximite|proche|accessibles?|a deux pas|"
                        r"a cote|a moins|a \d+)")
_NEG_BEFORE = re.compile(r"(loin|eloigne|eloignee|a l ecart|pas pres|pas a proximite|eviter|evitez|sans|pas de|"
                         r"pas d|aucun|aucune)\b[\w\s]{0,14}$")
_MINUTES = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:min|mn|minutes)\b")
_DISTANCE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(km|m)\b")
_REQUIRED = re.compile(r"\b(obligatoire|obligatoirement|imperatif|imperativement|absolument|indispensable|"
                       r"exige|exigee|uniquement|seulement|impose)\b")

_NUM = r"(\d{1,3}(?:[ .]\d{3})+|\d+(?:[.,]\d+)?)"
_CMP_MAX = r"moins de|maximum|max|jusqu a|<=|<|pas plus de|inferieur a|budget maximum|budget max|budget de|budget|au plus"
_CMP_MIN = r"plus de|au moins|minimum|mini|min|>=|>|superieur a|a partir de|des"
_UNIT = (r"k\s*€|k\s*euros?|k\b|m\s*€|millions?\s*d\s*euros|millions?\s*€|millions?|€|euros?|eur\b|"
         r"m2|m 2|metres? carres|ha\b|hectares?|pieces?|chambres?|ch\b")
_SINGLE = re.compile(rf"(?:(?P<cmp>{_CMP_MAX}|{_CMP_MIN})\s*)?{_NUM}\s*(?P<unit>{_UNIT})?")
_RANGE = re.compile(rf"(?:entre|de)\s*{_NUM}\s*(?P<u1>{_UNIT})?\s*(?:et|a|-)\s*{_NUM}\s*(?P<u2>{_UNIT})")

_TYPES: list[tuple[str, str]] = [
    ("terrain", r"\bterrains?\s+(?:a batir|constructibles?|seuls?|nus?)\b"),
    ("immeuble", r"\bimmeubles?\b"),
    ("appartement", r"\b(?:appartements?|appart|studios?|duplex|lofts?)\b"),
    ("maison", r"\b(?:maisons?|villas?|longeres?|pavillons?|fermettes?|corps de ferme|mas|bastides?|chalets?|"
               r"manoirs?|demeures?|chaumieres?|propriete|proprietes)\b"),
]


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return text


def _parse_area(text: str) -> tuple[SearchArea, str]:
    """Extrait le secteur (ville + rayon, régions, départements) et renvoie le texte sans ces mentions."""
    area = SearchArea()
    spans: list[tuple[int, int]] = []
    for pattern, kind in _AREA_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if kind == "radius_city":
            city, radius = m.group(2), float(m.group(1))
        elif kind == "city_radius":
            city, radius = m.group(1), float(m.group(2)) if m.group(2) else 15.0
        else:
            city, radius = m.group(1), 8.0
        city = city.strip(" -'’")
        n_city = normalize(city)
        # ne pas confondre une région / un département avec une ville
        if n_city != "paris" and (any(normalize(r) == n_city for r in REGIONS)
                                  or any(normalize(d) == n_city for d in DEPARTMENTS.values())):
            continue
        area.near, area.radius_km = city, radius
        spans.append(m.span())
        break
    text = _strip_spans(text, spans)

    norm = normalize(text)
    departments: list[str] = []
    names = {**{name: [code] for code, name in DEPARTMENTS.items()}, **REGIONS}
    for name in sorted(names, key=len, reverse=True):
        pat = rf"\b{_AREA_PREPOSITIONS}\s+" + term_pattern(name)
        if re.search(pat, norm):
            departments += [c for c in names[name] if c not in departments]
            norm = re.sub(pat, " ", norm)
    for m in re.finditer(r"(?:dans le|departement|dept\.?|\()\s*(\d{2}|2a|2b|97\d)\b\)?", norm):
        code = m.group(1).upper()
        if code in DEPARTMENTS and code not in departments:
            departments.append(code)
    norm = re.sub(r"(?:dans le|departement|dept\.?|\()\s*(?:\d{2}|2a|2b|97\d)\b\)?", " ", norm)
    area.departments = departments
    return area, norm


def _proximity(work: str) -> tuple[list[ProximityCriterion], str]:
    terms = {c.key: c.query_terms for c in CATEGORIES}
    hits, _ = find_term_hits(work, terms)
    params: list[Optional[dict]] = []
    linked: list[bool] = []
    for i, h in enumerate(hits):
        prev_end = hits[i - 1].end if i else 0
        next_start = hits[i + 1].start if i + 1 < len(hits) else len(work)
        before = work[prev_end:h.start]
        bounds = list(_BOUNDARY.finditer(before))
        if bounds:
            before = before[bounds[-1].end():]
        before = before[-45:]
        after = work[h.end:next_start]
        b = _BOUNDARY.search(after)
        if b:
            after = after[:b.start()]
        after = after[:35]
        linked.append(bool(i) and _CONNECTOR.match(work[prev_end:h.start]) is not None)

        cue = _CUE_BEFORE.search(before) or _CUE_AFTER.search(after)
        if not cue:
            params.append(None)
            continue
        window = f"{before} | {after}"
        p: dict = {}
        if _NEG_BEFORE.search(before):
            p["negative"] = True
        if mm := _MINUTES.search(after) or _MINUTES.search(before):
            p["max_minutes"] = parse_number(mm.group(1))
        if md := _DISTANCE.search(after) or _DISTANCE.search(before):
            meters = parse_number(md.group(1)) * (1000 if md.group(2) == "km" else 1)
            p["min_meters" if p.get("negative") else "max_meters"] = meters
        if "a pied" in window:
            p["mode"] = "pied"
        elif "velo" in window:
            p["mode"] = "velo"
        elif "voiture" in window:
            p["mode"] = "voiture"
        params.append(p)

    # « proche gare, commerces et écoles » : les termes reliés par « et », « , »… partagent les paramètres
    n = len(hits)
    for i in range(n):
        if params[i] is None:
            j = i
            while j > 0 and linked[j] and params[j] is None:
                j -= 1
            if params[j] is not None and all(linked[k] for k in range(j + 1, i + 1)):
                params[i] = dict(params[j])
                continue
            j = i
            while j + 1 < n and linked[j + 1] and params[j] is None:
                j += 1
            if params[j] is not None and all(linked[k] for k in range(i + 1, j + 1)):
                params[i] = dict(params[j])

    criteria: list[ProximityCriterion] = []
    seen: set[str] = set()
    for h, p in zip(hits, params):
        if p is None or h.key in seen:
            continue
        seen.add(h.key)
        cat = CATEGORIES_BY_KEY[h.key]
        if cat.nuisance or p.get("negative") or h.negated:
            default_min = 1500.0 if cat.key == "autoroute" else 400.0 if cat.key == "voie_ferree" else 1000.0
            criteria.append(ProximityCriterion(category=cat.key, min_meters=p.get("min_meters") or default_min))
            continue
        mode: TravelMode = p.get("mode") or ("voiture" if (p.get("max_minutes") or 0) > 20 else "pied")
        minutes = p.get("max_minutes")
        if minutes is None and p.get("max_meters") is None:
            minutes = cat.default_minutes
        criteria.append(ProximityCriterion(category=cat.key, max_minutes=minutes, mode=mode,
                                           max_meters=p.get("max_meters")))
    for h, p in zip(hits, params):
        if p is not None:
            work = work[:h.start] + "#" * (h.end - h.start) + work[h.end:]
    work = re.sub(r"\d+(?:[.,]\d+)?\s*(?:min|mn|minutes|km|m)\b", lambda m: " " * len(m.group(0)), work)
    return criteria, work


def _apply_number(q: SearchQuery, value: float, unit: str, cmp: str, context: str) -> bool:
    unit = (unit or "").strip()
    is_max = bool(cmp) and re.fullmatch(_CMP_MAX, cmp) is not None
    is_min = bool(cmp) and not is_max
    if re.match(r"k", unit):
        value, unit = value * 1000, "€"
    elif re.match(r"m\s*€|million", unit):
        value, unit = value * 1_000_000, "€"
    if unit in ("€", "eur") or unit.startswith("euro") or (not unit and value >= 10_000):
        if is_min:
            q.price_min = value
        else:
            q.price_max = value
        return True
    if unit in ("ha",) or unit.startswith("hectare"):
        value, unit = value * 10_000, "terrain"
    if unit.startswith(("m2", "m 2", "metre")) or unit == "terrain":
        land = (unit == "terrain"
                or re.search(r"(terrain|jardin|parcelle|foncier|exterieur)[\w\s]{0,12}$", context)
                or (value >= 1000 and "habitable" not in context))
        if land:
            if is_max:
                q.land_max = value
            else:
                q.land_min = value
        elif is_max:
            q.surface_max = value
        else:
            q.surface_min = value
        return True
    if unit.startswith("piece"):
        q.rooms_min = int(value)
        return True
    if unit.startswith("chambre") or unit == "ch":
        q.bedrooms_min = int(value)
        return True
    return False


def _numbers(q: SearchQuery, work: str, base: str) -> str:
    """Prix, surfaces, pièces. `base` = même texte avant masquage (même longueur), pour le contexte."""
    for m in list(_RANGE.finditer(work)):
        lo, hi = parse_number(m.group(1)), parse_number(m.group(3))
        unit = m.group("u2") or m.group("u1") or ""
        if lo is None or hi is None:
            continue
        ctx = base[max(0, m.start() - 25):m.start()]
        if _apply_number(q, lo, m.group("u1") or unit, "plus de", ctx):
            _apply_number(q, hi, unit, "moins de", ctx)
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]
    for m in list(_SINGLE.finditer(work)):
        value = parse_number(m.group(2))
        if value is None:
            continue
        ctx = base[max(0, m.start() - 25):m.start()]
        if _apply_number(q, value, m.group("unit") or "", (m.group("cmp") or "").strip(), ctx):
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]
    if m := re.search(r"\b[tf]([1-9])\b", work):
        q.rooms_min = q.rooms_min or int(m.group(1))
    return work


def _visual(work: str, multiword: bool) -> tuple[list[VisualCriterion], str]:
    terms = {f.key: [t for t in f.query_terms if (" " in normalize(t)) == multiword] for f in FEATURES}
    hits, masked = find_term_hits(work, terms)
    out: list[VisualCriterion] = []
    for h in hits:
        clause = re.split(r"[.,;]", work[h.start:])[0]
        before_clause = re.split(r"[.,;]", work[:h.start])[-1]
        required = bool(_REQUIRED.search(clause) or _REQUIRED.search(before_clause[-25:]))
        exclude = h.negated or FEATURES_BY_KEY[h.key].undesirable
        out.append(VisualCriterion(feature=h.key, required=required and not exclude, exclude=exclude))
    return out, masked


def parse_rules(text: str) -> SearchQuery:
    q = SearchQuery(text=text)
    q.keywords = [a or b for a, b in re.findall(r'"([^"]+)"|«\s*([^»]+?)\s*»', text)]
    clean = re.sub(r'"[^"]+"|«[^»]+»', " ", text)

    norm_full = normalize(clean)
    for ptype, pattern in _TYPES:
        if re.search(pattern, norm_full) and ptype not in q.property_types:
            q.property_types.append(ptype)  # type: ignore[arg-type]

    q.area, work = _parse_area(clean)
    base = work
    visual1, work = _visual(work, multiword=True)
    q.proximity, work = _proximity(work)
    work = _numbers(q, work, base)
    visual2, work = _visual(work, multiword=False)

    seen: set[str] = set()
    for v in visual1 + visual2:
        if v.feature not in seen:
            seen.add(v.feature)
            q.visual.append(v)

    if re.search(r"moins chers?|prix croissant|les moins chers", norm_full):
        q.sort = "price_asc"
    elif re.search(r"plus recentes?|derniers? (?:annonces|ajouts)|nouveautes", norm_full):
        q.sort = "recent"
    return q


# ----------------------------------------------------------------------------------------
# Analyse par Claude
# ----------------------------------------------------------------------------------------

FeatureKey = Literal[FEATURE_KEYS]  # type: ignore[valid-type]
CategoryKey = Literal[CATEGORY_KEYS]  # type: ignore[valid-type]


class _PVisual(BaseModel):
    feature: FeatureKey
    required: bool
    exclude: bool


class _PFree(BaseModel):
    question: str
    required: bool


class _PProx(BaseModel):
    category: CategoryKey
    max_minutes: Optional[float]
    mode: Literal["pied", "velo", "voiture"]
    max_meters: Optional[float]
    min_meters: Optional[float]
    required: bool


class _PArea(BaseModel):
    near: Optional[str]
    radius_km: Optional[float]
    departments: list[str]


class _PQuery(BaseModel):
    property_types: list[PropertyType]
    price_min: Optional[float]
    price_max: Optional[float]
    surface_min: Optional[float]
    surface_max: Optional[float]
    land_min: Optional[float]
    land_max: Optional[float]
    rooms_min: Optional[int]
    bedrooms_min: Optional[int]
    area: _PArea
    visual: list[_PVisual]
    visual_free: list[_PFree]
    proximity: list[_PProx]
    keywords: list[str]
    sort: Literal["score", "price_asc", "price_desc", "recent", "surface_desc"]


def _parser_system_prompt() -> str:
    feats = "\n".join(f"- {f.key} : {f.label}" + (" (indésirable)" if f.undesirable else "") for f in FEATURES)
    cats = "\n".join(
        f"- {c.key} : {c.label}" + (" — nuisance : utiliser min_meters" if c.nuisance else
                                    f" (par défaut {c.default_minutes:g} min à pied)")
        for c in CATEGORIES)
    return f"""Tu convertis une recherche immobilière écrite en français en critères structurés pour un \
moteur de recherche. Ne garde que ce que l'utilisateur demande ; laisse null / listes vides sinon.

Règles :
- Prix en euros (« 300k€ » = 300000), surfaces en m², 1 ha = 10000 m² de terrain (land_min).
- property_types : maison (longère, villa, mas, fermette, chalet…), appartement, terrain (à bâtir), immeuble.
- area : une ville → near + radius_km (« à Rennes » : 8 ; « autour de / près de Rennes » : 15, ou le rayon \
indiqué). Une région ou un département → departments (codes INSEE : « Bretagne » → 22, 29, 35, 56).
- visual : caractéristiques visibles sur les photos, à choisir dans la liste ci-dessous. exclude = vrai si \
l'utilisateur veut l'éviter (« pas de vis-à-vis » → haies_intimite, ou exclude sur vis_a_vis).
- visual_free : tout autre critère visible sur photo absent de la liste (« volets bleus », « glycine sur la \
façade ») reformulé en courte phrase vérifiable.
- proximity : catégories ci-dessous. « proche de la gare » → max_minutes par défaut, mode pied ; « à 10 min en \
voiture du supermarché » → max_minutes 10, mode voiture ; « à moins de 500 m » → max_meters 500 ; « loin de \
l'autoroute » → min_meters 1500 (voie ferrée : 400).
- required = vrai uniquement si l'utilisateur insiste (obligatoire, impératif, absolument, uniquement).
- keywords : seulement des mots à retrouver tels quels dans le texte de l'annonce et qui ne relèvent ni des \
photos ni de la localisation (ex. « viager », « DPE A »).
- sort : « score » sauf demande explicite (« les moins chers » → price_asc, « les plus récentes » → recent).

Caractéristiques visuelles :
{feats}

Catégories de proximité :
{cats}
"""


def parse_with_claude(text: str, client=None) -> SearchQuery:
    import anthropic

    client = client or anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=config.CLAUDE_MODEL,
        max_tokens=8000,
        system=[{"type": "text", "text": _parser_system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": text}],
        output_format=_PQuery,
        output_config={"effort": config.PARSER_EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"Analyse de la requête impossible ({response.stop_reason})")
    out: _PQuery = response.parsed_output
    data = out.model_dump()
    data["area"] = SearchArea(**data["area"])
    data["visual"] = [VisualCriterion(**v) for v in data["visual"]]
    data["visual_free"] = [FreeVisualCriterion(**v) for v in data["visual_free"]]
    data["proximity"] = [ProximityCriterion(**p) for p in data["proximity"]]
    return SearchQuery(text=text, **data)


def parse_query(text: str, use_claude: Optional[bool] = None) -> tuple[SearchQuery, str]:
    """Renvoie (requête structurée, analyseur utilisé : « claude » ou « règles »)."""
    text = (text or "").strip()
    if not text:
        return SearchQuery(), "vide"
    if use_claude is None:
        use_claude = config.claude_available()
    if use_claude:
        try:
            return parse_with_claude(text), "claude"
        except Exception as exc:  # réseau, clé invalide… : on retombe sur les règles
            log.warning("Analyse Claude indisponible, utilisation des règles : %s", exc)
    return parse_rules(text), "règles"
