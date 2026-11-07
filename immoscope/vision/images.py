"""Téléchargement et préparation des photos pour l'analyse visuelle."""
from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import Optional

import httpx

from .. import config

log = logging.getLogger(__name__)

SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}

try:  # Pillow est recommandé (redimensionnement, conversion), mais pas indispensable.
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def images_hash(urls: list[str]) -> str:
    return hashlib.sha1("\n".join(urls).encode()).hexdigest()[:16]


def _prepare(data: bytes, content_type: str) -> Optional[tuple[str, str]]:
    """Renvoie (media_type, base64) en réduisant l'image si possible."""
    if Image is not None:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            side = config.VISION_MAX_SIDE
            if max(img.size) > side:
                img.thumbnail((side, side))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()
        except Exception as exc:  # image corrompue ou format exotique
            log.debug("Pillow n'a pas pu lire l'image : %s", exc)
    ctype = content_type.split(";")[0].strip().lower()
    if ctype in SUPPORTED and len(data) < 4_500_000:
        return ctype, base64.standard_b64encode(data).decode()
    return None


def image_block(url: str, client: httpx.Client, referer: Optional[str] = None) -> Optional[dict]:
    """Bloc image pour l'API Claude : base64 si le téléchargement réussit, sinon URL directe."""
    if not url.startswith(("http://", "https://")):
        return None
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/126.0 Safari/537.36",
               "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    try:
        resp = client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        prepared = _prepare(resp.content, resp.headers.get("content-type", ""))
        if prepared:
            media_type, data = prepared
            return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    except httpx.HTTPError as exc:
        log.info("Téléchargement impossible (%s) : %s — envoi de l'URL à Claude", url, exc)
    return {"type": "image", "source": {"type": "url", "url": url}}
