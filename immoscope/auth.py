"""Accès protégé par mot de passe (activé dès que `IMMO_PASSWORD` est défini).

Session : cookie signé (HMAC), valable 30 jours, invalidé si le mot de passe change.
Marque-page : jeton personnel dérivé du secret, car un envoi depuis un autre site ne transporte pas le cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from . import config

COOKIE = "immoscope_session"
MAX_AGE = 30 * 86400


def enabled() -> bool:
    return bool(config.PASSWORD)


def _secret() -> bytes:
    base = config.SECRET_KEY or f"immoscope:{config.PASSWORD}"
    return hashlib.sha256(base.encode()).digest()


def _password_tag() -> str:
    return hashlib.sha256(f"tag:{config.PASSWORD}".encode()).hexdigest()[:16]


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    payload = f"{int(time.time()) + MAX_AGE}|{_password_tag()}"
    return f"{payload}|{_sign(payload)}"


def valid_session(value: Optional[str]) -> bool:
    if not value or value.count("|") != 2:
        return False
    expires, tag, sig = value.split("|")
    payload = f"{expires}|{tag}"
    if not hmac.compare_digest(sig, _sign(payload)) or not hmac.compare_digest(tag, _password_tag()):
        return False
    return expires.isdigit() and int(expires) > time.time()


def check_password(candidate: str) -> bool:
    return bool(config.PASSWORD) and hmac.compare_digest(candidate.encode(), config.PASSWORD.encode())


def import_token() -> str:
    return _sign("bookmarklet")[:32]
