"""Outils de normalisation et de recherche de termes en français."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

_NEGATIONS = ("pas de ", "pas d ", "aucun ", "aucune ", "sans ", "ni ", "pas trop de ", "eviter ", "evitez ",
              "pas de la ", "pas pres de ", "pas pres d ")


def normalize(text: str) -> str:
    """Minuscules, sans accents, apostrophes et ponctuation légère homogénéisées.

    La longueur n'est pas forcément conservée : travailler ensuite sur le texte normalisé.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'").replace("œ", "oe").replace("²", "2")
    text = re.sub(r"[\'`´]", " ", text)
    text = re.sub(r"[^\w\s\-+<>€%.,/;:!?()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def term_pattern(expr: str) -> str:
    """Expression régulière tolérante (espaces / tirets interchangeables) pour un terme normalisé."""
    words = [re.escape(w) for w in re.split(r"[\s\-]+", normalize(expr)) if w]
    return r"(?<![\w])" + r"[\s\-]+".join(words) + r"(?![\w])"


@dataclass
class TermHit:
    key: str
    expr: str
    start: int
    end: int
    negated: bool


def find_term_hits(norm: str, terms: dict[str, Iterable[str]]) -> tuple[list[TermHit], str]:
    """Cherche les termes dans un texte DÉJÀ normalisé.

    Les expressions les plus longues sont testées d'abord et la zone trouvée est masquée, pour que
    « sans vis-à-vis » ne déclenche pas aussi « vis-à-vis ». Renvoie les occurrences et le texte masqué.
    """
    candidates: list[tuple[str, str]] = []
    for key, expressions in terms.items():
        for expr in expressions:
            n = normalize(expr)
            if n:
                candidates.append((n, key))
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    hits: list[TermHit] = []
    masked = norm
    for n, key in candidates:
        for match in re.finditer(term_pattern(n), masked):
            start, end = match.start(), match.end()
            before = masked[max(0, start - 14):start]
            negated = not n.startswith(("sans ", "pas ", "aucun")) and any(before.endswith(neg) for neg in _NEGATIONS)
            hits.append(TermHit(key, n, start, end, negated))
            masked = masked[:start] + "#" * (end - start) + masked[end:]
    hits.sort(key=lambda h: h.start)
    return hits, masked


def find_terms(text: str, terms: dict[str, Iterable[str]], *, skip_negated: bool = True) -> dict[str, str]:
    """{clé: expression trouvée} pour les termes présents (et non niés) dans `text`."""
    hits, _ = find_term_hits(normalize(text), terms)
    found: dict[str, str] = {}
    for h in hits:
        if skip_negated and h.negated:
            continue
        found.setdefault(h.key, h.expr)
    return found


def parse_number(raw: str) -> float | None:
    """« 250 000 », « 250.000 », « 1,5 » → nombre."""
    s = raw.strip().replace(" ", " ").replace("\xa0", " ")
    s = re.sub(r"(?<=\d)[\s.](?=\d{3}(\D|$))", "", s)  # séparateurs de milliers
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None
