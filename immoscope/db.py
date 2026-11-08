"""Stockage des annonces, analyses et caches.

- SQLite par défaut (fichier local, idéal sur votre ordinateur) ;
- PostgreSQL si `DATABASE_URL` est défini (Supabase, Render Postgres…), pour l'hébergement en ligne.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config
from .models import Listing, LocationAnalysis, VisionAnalysis

log = logging.getLogger(__name__)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS listings (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        url TEXT,
        data TEXT NOT NULL,
        raw TEXT,
        imported_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS vision (
        listing_id TEXT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
        images_hash TEXT,
        data TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS location (
        listing_id TEXT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
        coords TEXT,
        data TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS visual_checks (
        listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (listing_id, question)
    )""",
    """CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        data TEXT NOT NULL
    )""",
]

RAW_MAX_CHARS = 100_000


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def is_postgres_url(url: Optional[str]) -> bool:
    return bool(url) and url.startswith(("postgres://", "postgresql://"))


class Database:
    def __init__(self, path: Path | str | None = None, *, url: Optional[str] = None):
        """`url` (ou `DATABASE_URL`) PostgreSQL prioritaire ; sinon fichier SQLite `path`."""
        if url is None and path is None:
            url = config.DATABASE_URL
        self.kind = "postgres" if is_postgres_url(url) else "sqlite"
        self.url = url
        self.path = None if self.kind == "postgres" else Path(path or config.DB_PATH)
        self._lock = threading.RLock()
        self.conn: Any = None
        self._connect()
        with self._lock:
            for stmt in SCHEMA:
                self._run(stmt, ())

    # -- connexion -----------------------------------------------------------------
    def _connect(self) -> None:
        if self.kind == "postgres":
            import psycopg
            from psycopg.rows import dict_row

            # prepare_threshold=None : compatible avec les poolers en mode transaction (Supabase, PgBouncer)
            self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row, prepare_threshold=None,
                                        connect_timeout=15)
        else:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self.conn.row_factory = _dict_factory
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def _retry(self, fn):
        """Exécute `fn`, en se reconnectant une fois si la connexion Postgres a été coupée."""
        with self._lock:
            if self.kind != "postgres":
                return fn()
            import psycopg
            try:
                return fn()
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                log.info("Connexion à la base perdue (%s), reconnexion…", exc)
                try:
                    self.conn.close()
                except Exception:  # pragma: no cover
                    pass
                self._connect()
                return fn()

    def _run(self, sql: str, params: Iterable = ()) -> int:
        def go():
            cur = self.conn.execute(self._sql(sql), tuple(params))
            if self.kind == "sqlite":
                self.conn.commit()
            return cur.rowcount
        return self._retry(go)

    def _all(self, sql: str, params: Iterable = ()) -> list[dict]:
        return self._retry(lambda: list(self.conn.execute(self._sql(sql), tuple(params)).fetchall()))

    def _one(self, sql: str, params: Iterable = ()) -> Optional[dict]:
        return self._retry(lambda: self.conn.execute(self._sql(sql), tuple(params)).fetchone())

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self.kind == "postgres":
                with self.conn.transaction():
                    yield
            else:
                with self.conn:
                    yield

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- annonces --------------------------------------------------------------------
    def upsert_listing(self, listing: Listing) -> bool:
        """Ajoute ou met à jour une annonce. Renvoie True si elle est nouvelle."""
        data = listing.model_dump_json(exclude={"raw"})
        raw = json.dumps(listing.raw, ensure_ascii=False, default=str) if listing.raw else None
        if raw and len(raw) > RAW_MAX_CHARS:
            raw = None  # données brutes trop volumineuses : inutiles pour la recherche
        ts = now_iso()

        def go():
            with self._transaction():
                existing = self.conn.execute(self._sql("SELECT 1 AS x FROM listings WHERE id = ?"), (listing.id,)).fetchone()
                if existing:
                    self.conn.execute(self._sql("UPDATE listings SET source=?, url=?, data=?, raw=?, updated_at=? WHERE id=?"),
                                      (listing.source, listing.url, data, raw, ts, listing.id))
                else:
                    self.conn.execute(self._sql(
                        "INSERT INTO listings (id, source, url, data, raw, imported_at, updated_at) VALUES (?,?,?,?,?,?,?)"),
                        (listing.id, listing.source, listing.url, data, raw, ts, ts))
            return not existing
        return self._retry(go)

    def get_listing(self, listing_id: str) -> Optional[Listing]:
        row = self._one("SELECT data FROM listings WHERE id = ?", (listing_id,))
        return Listing.model_validate_json(row["data"]) if row else None

    def list_listings(self) -> list[Listing]:
        rows = self._all("SELECT data FROM listings ORDER BY imported_at DESC")
        return [Listing.model_validate_json(r["data"]) for r in rows]

    def imported_at(self) -> dict[str, str]:
        return {r["id"]: r["imported_at"] for r in self._all("SELECT id, imported_at FROM listings")}

    def delete_listing(self, listing_id: str) -> bool:
        return self._run("DELETE FROM listings WHERE id = ?", (listing_id,)) > 0

    def delete_source(self, source: str) -> int:
        return self._run("DELETE FROM listings WHERE source = ?", (source,))

    def count(self) -> int:
        return int(self._one("SELECT COUNT(*) AS n FROM listings")["n"])

    # -- analyses visuelles ------------------------------------------------------------
    def save_vision(self, analysis: VisionAnalysis, images_hash: str) -> None:
        self._run("INSERT INTO vision (listing_id, images_hash, data) VALUES (?,?,?) "
                  "ON CONFLICT (listing_id) DO UPDATE SET images_hash = excluded.images_hash, data = excluded.data",
                  (analysis.listing_id, images_hash, analysis.model_dump_json()))

    def get_vision(self, listing_id: str) -> Optional[VisionAnalysis]:
        row = self._one("SELECT data FROM vision WHERE listing_id = ?", (listing_id,))
        return VisionAnalysis.model_validate_json(row["data"]) if row else None

    def all_vision(self) -> dict[str, VisionAnalysis]:
        return {r["listing_id"]: VisionAnalysis.model_validate_json(r["data"])
                for r in self._all("SELECT listing_id, data FROM vision")}

    def vision_hashes(self) -> dict[str, str]:
        return {r["listing_id"]: r["images_hash"] for r in self._all("SELECT listing_id, images_hash FROM vision")}

    # -- localisation -----------------------------------------------------------------
    def save_location(self, analysis: LocationAnalysis, coords_key: str) -> None:
        self._run("INSERT INTO location (listing_id, coords, data) VALUES (?,?,?) "
                  "ON CONFLICT (listing_id) DO UPDATE SET coords = excluded.coords, data = excluded.data",
                  (analysis.listing_id, coords_key, analysis.model_dump_json()))

    def get_location(self, listing_id: str) -> Optional[LocationAnalysis]:
        row = self._one("SELECT data FROM location WHERE listing_id = ?", (listing_id,))
        return LocationAnalysis.model_validate_json(row["data"]) if row else None

    def all_locations(self) -> dict[str, LocationAnalysis]:
        return {r["listing_id"]: LocationAnalysis.model_validate_json(r["data"])
                for r in self._all("SELECT listing_id, data FROM location")}

    def location_keys(self) -> dict[str, str]:
        return {r["listing_id"]: r["coords"] for r in self._all("SELECT listing_id, coords FROM location")}

    # -- vérifications visuelles libres ------------------------------------------------
    def save_visual_check(self, listing_id: str, question: str, result: dict) -> None:
        self._run("INSERT INTO visual_checks (listing_id, question, data) VALUES (?,?,?) "
                  "ON CONFLICT (listing_id, question) DO UPDATE SET data = excluded.data",
                  (listing_id, question, json.dumps(result, ensure_ascii=False)))

    def visual_checks(self, questions: Iterable[str]) -> dict[tuple[str, str], dict]:
        qs = list(questions)
        if not qs:
            return {}
        marks = ",".join("?" * len(qs))
        rows = self._all(f"SELECT listing_id, question, data FROM visual_checks WHERE question IN ({marks})", qs)
        return {(r["listing_id"], r["question"]): json.loads(r["data"]) for r in rows}

    # -- cache générique (géocodage, Overpass) -----------------------------------------
    def cache_get(self, key: str, max_age_days: float | None = None):
        row = self._one("SELECT created_at, data FROM cache WHERE key = ?", (key,))
        if not row:
            return None
        if max_age_days is not None:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
            if age.total_seconds() > max_age_days * 86400:
                return None
        return json.loads(row["data"])

    def cache_set(self, key: str, value) -> None:
        self._run("INSERT INTO cache (key, created_at, data) VALUES (?,?,?) "
                  "ON CONFLICT (key) DO UPDATE SET created_at = excluded.created_at, data = excluded.data",
                  (key, now_iso(), json.dumps(value, ensure_ascii=False)))

    def cache_entries(self, prefix: str) -> list[tuple[str, str]]:
        """(clé, date) des entrées dont la clé commence par `prefix`, les plus récentes d'abord."""
        rows = self._all("SELECT key, created_at FROM cache WHERE key LIKE ? ORDER BY created_at DESC, key DESC",
                         (prefix.replace("%", "") + "%",))
        return [(r["key"], r["created_at"]) for r in rows]

    def cache_delete(self, key: str) -> None:
        self._run("DELETE FROM cache WHERE key = ?", (key,))

    def clear(self) -> None:
        """Vide toutes les tables (tests)."""
        for table in ("visual_checks", "vision", "location", "cache", "listings"):
            self._run(f"DELETE FROM {table}")


_db: Optional[Database] = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database()
        return _db


def set_db(db: Optional[Database]) -> None:
    global _db
    _db = db
