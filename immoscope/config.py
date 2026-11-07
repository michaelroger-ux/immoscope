"""Configuration, lue dans les variables d'environnement (voir .env.example)."""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Charge un fichier .env minimal (CLE=valeur) s'il existe, sans écraser l'environnement."""
    path = Path(os.getenv("IMMO_ENV_FILE", ".env"))
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DATA_DIR = Path(os.getenv("IMMO_DATA_DIR", "data")).resolve()
# Vrai quand le dossier de données est choisi explicitement (disque persistant Render : /data).
DATA_DIR_FROM_ENV = bool(os.getenv("IMMO_DATA_DIR"))
DB_PATH = Path(os.getenv("IMMO_DB_PATH", str(DATA_DIR / "immoscope.db")))
# Base PostgreSQL (Supabase, Render…) : prioritaire sur le fichier SQLite si définie.
DATABASE_URL = os.getenv("DATABASE_URL") or None

# Mot de passe d'accès : obligatoire dès que l'application est en ligne.
PASSWORD = os.getenv("IMMO_PASSWORD") or None
SECRET_KEY = os.getenv("IMMO_SECRET_KEY") or None
# Vrai sur Render (variable définie par la plateforme).
ON_RENDER = bool(os.getenv("RENDER"))

# Modèle Claude utilisé pour l'analyse des photos et la compréhension des requêtes.
CLAUDE_MODEL = os.getenv("IMMO_CLAUDE_MODEL", "claude-opus-5-5")
VISION_EFFORT = os.getenv("IMMO_VISION_EFFORT", "medium")
PARSER_EFFORT = os.getenv("IMMO_PARSER_EFFORT", "low")
# Nombre maximum de photos envoyées par annonce (coût / pertinence).
VISION_MAX_IMAGES = int(os.getenv("IMMO_VISION_MAX_IMAGES", "8"))
# Taille max (px, plus grand côté) des photos envoyées : réduit le coût sans perdre l'essentiel.
VISION_MAX_SIDE = int(os.getenv("IMMO_VISION_MAX_SIDE", "1280"))

# Géocodage : Géoplateforme IGN (France, successeur de api-adresse.data.gouv.fr) puis Nominatim (monde).
GEOCODER_FR_URL = os.getenv("IMMO_GEOCODER_FR_URL", "https://data.geopf.fr/geocodage/search")
NOMINATIM_URL = os.getenv("IMMO_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
# Points d'intérêt (transports, commerces…) depuis OpenStreetMap.
OVERPASS_URL = os.getenv("IMMO_OVERPASS_URL", "https://overpass-api.de/api/interpreter")

USER_AGENT = os.getenv("IMMO_USER_AGENT", "Immoscope/0.1 (outil personnel de recherche immobiliere)")
HTTP_TIMEOUT = float(os.getenv("IMMO_HTTP_TIMEOUT", "30"))


def claude_available() -> bool:
    """Vrai si une clé (ou un profil `ant auth login`) semble configuré pour l'API Claude."""
    if os.getenv("IMMO_DISABLE_CLAUDE"):
        return False
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"):
        return True
    return (Path.home() / ".config" / "anthropic").is_dir()
