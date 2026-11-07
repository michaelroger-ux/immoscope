"""Ligne de commande : `immoscope serve | demo | import | process | search`."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="immoscope", description="Recherche immobilière intelligente")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Lancer l'interface web")
    s.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))

    sub.add_parser("demo", help="Charger les annonces de démonstration")

    i = sub.add_parser("import", help="Importer une URL d'annonce, un fichier HTML, JSON ou CSV")
    i.add_argument("source", help="URL, ou chemin d'un fichier .html / .json / .csv")
    i.add_argument("--url", help="URL d'origine (pour un fichier .html enregistré)")

    p = sub.add_parser("process", help="Analyser les photos et la localisation des annonces en attente")
    p.add_argument("--no-vision", action="store_true", help="Ne pas analyser les photos")
    p.add_argument("--no-location", action="store_true", help="Ne pas analyser la localisation")
    p.add_argument("--force", action="store_true", help="Refaire les analyses existantes")

    q = sub.add_parser("search", help="Rechercher en langage naturel")
    q.add_argument("text", nargs="+")
    q.add_argument("--strict", action="store_true", help="Seulement les annonces qui remplissent tous les critères")
    q.add_argument("--rules", action="store_true", help="Ne pas utiliser Claude pour comprendre la requête")
    q.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "serve":
        import uvicorn
        print(f"Immoscope : http://{args.host}:{args.port}")
        uvicorn.run("immoscope.api:app", host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")
        return 0

    if args.cmd == "demo":
        from .sources.demo import load_demo
        print(f"{load_demo()} annonces de démonstration chargées.")
        return 0

    if args.cmd == "import":
        from .sources.importers import ImportBlocked, import_html, import_records, import_url
        src = args.source
        try:
            if src.startswith(("http://", "https://")):
                result = import_url(src)
            elif src.lower().endswith((".html", ".htm")):
                result = import_html(Path(src).read_text(encoding="utf-8", errors="replace"), args.url)
            else:
                result = import_records(Path(src).read_bytes(), src)
        except ImportBlocked as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"{result['found']} annonce(s) trouvée(s), {result['new']} nouvelle(s).")
        return 0

    if args.cmd == "process":
        from .pipeline import JOB, start_processing
        res = start_processing(vision=not args.no_vision, location=not args.no_location, force=args.force)
        if not res.get("started"):
            print(res.get("error") or res.get("message"))
            return 0 if not res.get("error") else 1
        last = -1
        while True:
            snap = JOB.snapshot()
            if snap["done"] != last:
                last = snap["done"]
                print(f"  {snap['done']}/{snap['total']} — {snap['current'] or ''}")
            if not snap["running"]:
                break
            time.sleep(0.5)
        for m in snap["messages"]:
            print("  !", m)
        print(f"Terminé : {snap['done'] - snap['errors']} réussite(s), {snap['errors']} échec(s).")
        return 0

    if args.cmd == "search":
        from .search.engine import search
        from .search.query_parser import parse_query
        text = " ".join(args.text)
        query, used = parse_query(text, use_claude=False if args.rules else None)
        result = search(query, strict=args.strict)
        if args.json:
            print(json.dumps({"query": query.model_dump(), **result}, ensure_ascii=False, indent=2, default=str))
            return 0
        print(f"Requête comprise ({used}) :")
        print("  " + json.dumps(query.model_dump(exclude_defaults=True, exclude={"text"}), ensure_ascii=False))
        print(f"{result['matched']} annonce(s) sur {result['total']}\n")
        for r in result["results"][:20]:
            lst = r["listing"]
            score = f"{round(r['score'] * 100)} %" if r["score"] is not None else "—"
            price = f"{lst['price']:,.0f} €".replace(",", " ") if lst.get("price") else "prix ?"
            print(f"[{score:>5}] {lst['title']} — {lst.get('city') or ''} — {price}")
            for c in r["criteria"]:
                mark = {"ok": "✓", "partiel": "~", "non": "✗", "inconnu": "?"}[c["status"]]
                print(f"        {mark} {c['label']} : {c['detail']}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
