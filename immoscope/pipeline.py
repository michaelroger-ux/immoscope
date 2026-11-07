"""Traitements longs (analyse des photos, localisation, vérifications visuelles) en arrière-plan."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from . import config
from .db import Database, get_db, now_iso
from .location.enrich import coords_key, enrich_listing, resolve_coordinates
from .models import Listing
from .vision.images import images_hash

log = logging.getLogger(__name__)


def _http_images(listing: Listing) -> list[str]:
    return [u for u in listing.images if u.startswith(("http://", "https://"))][: config.VISION_MAX_IMAGES]


def needs_vision(listing: Listing, db: Database, hashes: dict[str, str], visions: dict) -> bool:
    urls = _http_images(listing)
    if not urls:
        return False
    v = visions.get(listing.id)
    return v is None or bool(v.error) or hashes.get(listing.id) != images_hash(urls)


def needs_location(listing: Listing, locations: dict) -> bool:
    loc = locations.get(listing.id)
    return loc is None or bool(loc.error)


def pending(db: Optional[Database] = None) -> dict:
    db = db or get_db()
    listings = db.list_listings()
    visions, hashes, locations = db.all_vision(), db.vision_hashes(), db.all_locations()
    return {
        "vision": [x.id for x in listings if needs_vision(x, db, hashes, visions)],
        "location": [x.id for x in listings if needs_location(x, locations)],
    }


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "kind": None, "total": 0, "done": 0, "errors": 0, "current": None,
                      "started_at": None, "finished_at": None, "messages": []}

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.state, "messages": list(self.state["messages"][-20:])}

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _msg(self, text: str):
        with self.lock:
            self.state["messages"].append(text)

    def start(self, kind: str, tasks: list, worker) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "kind": kind, "total": len(tasks), "done": 0, "errors": 0,
                          "current": None, "started_at": now_iso(), "finished_at": None, "messages": []}

        def run():
            try:
                for task in tasks:
                    self._set(current=str(task[1] if isinstance(task, tuple) else task))
                    try:
                        ok, message = worker(task)
                    except Exception as exc:  # une annonce en échec ne bloque pas les suivantes
                        log.exception("Échec du traitement %s", task)
                        ok, message = False, f"{task} : {exc}"
                    with self.lock:
                        self.state["done"] += 1
                        if not ok:
                            self.state["errors"] += 1
                    if message:
                        self._msg(message)
            finally:
                self._set(running=False, current=None, finished_at=now_iso())

        threading.Thread(target=run, daemon=True).start()
        return True


JOB = Job()


def start_processing(*, vision: bool = True, location: bool = True, ids: Optional[list[str]] = None,
                     force: bool = False, db: Optional[Database] = None) -> dict:
    db = db or get_db()
    todo = pending(db)
    if ids is not None:
        wanted = set(ids)
        todo = {"vision": [i for i in (ids if force else todo["vision"]) if i in wanted],
                "location": [i for i in (ids if force else todo["location"]) if i in wanted]}
    tasks: list[tuple[str, str]] = []
    if location:
        tasks += [("location", i) for i in todo["location"]]
    if vision:
        if not config.claude_available():
            return {"started": False, "error": "Analyse des photos indisponible : configurez ANTHROPIC_API_KEY.",
                    "pending": {k: len(v) for k, v in todo.items()}}
        tasks += [("vision", i) for i in todo["vision"]]
    if not tasks:
        return {"started": False, "error": None, "message": "Rien à traiter."}

    def worker(task):
        kind, lid = task
        listing = db.get_listing(lid)
        if listing is None:
            return True, None
        if kind == "location":
            res = enrich_listing(listing, db=db, force=force)
            time.sleep(1.0)  # politesse envers les services OpenStreetMap publics
            return (not res.error), (f"{listing.title[:50]} : {res.error}" if res.error else None)
        if not _http_images(listing):
            return True, None  # annonces de démonstration ou sans photo
        from .vision.claude_tagger import analyze_listing
        res = analyze_listing(listing, db=db, force=force)
        return (not res.error), (f"{listing.title[:50]} : {res.error}" if res.error else None)

    started = JOB.start("traitement", tasks, worker)
    return {"started": started, "error": None if started else "Un traitement est déjà en cours.",
            "tasks": len(tasks)}


def start_visual_checks(question: str, ids: list[str], db: Optional[Database] = None) -> dict:
    db = db or get_db()
    if not config.claude_available():
        return {"started": False, "error": "Vérification indisponible : configurez ANTHROPIC_API_KEY."}
    from .vision.claude_tagger import visual_check

    def worker(lid):
        listing = db.get_listing(lid)
        if listing is None:
            return True, None
        res = visual_check(listing, question, db=db)
        return (not res.get("error")), res.get("error")

    started = JOB.start("vérification visuelle", list(ids), worker)
    return {"started": started, "error": None if started else "Un traitement est déjà en cours.", "tasks": len(ids)}


__all__ = ["JOB", "pending", "start_processing", "start_visual_checks", "coords_key", "resolve_coordinates"]
