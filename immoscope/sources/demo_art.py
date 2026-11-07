"""Illustrations SVG générées pour les annonces de démonstration (façade, jardin, intérieur)."""
from __future__ import annotations

import random
from html import escape

W, H = 800, 560


def _stone_pattern(rng: random.Random, light: bool) -> str:
    base = ["#b9ab95", "#a89b86", "#c8bca6", "#9d907c", "#b3a58c"] if light else \
           ["#8f8c86", "#a19c93", "#7d7a74", "#b0aa9f", "#959086"]
    rects = []
    y = 0
    while y < 60:
        h = rng.randint(9, 14)
        x = -rng.randint(0, 10)
        while x < 60:
            w = rng.randint(12, 22)
            rects.append(f'<rect x="{x}" y="{y}" width="{w - 2}" height="{h - 2}" rx="3" fill="{rng.choice(base)}"/>')
            x += w
        y += h
    mortar = "#d9d0c0" if light else "#c9c4bb"
    return (f'<pattern id="facade" width="60" height="60" patternUnits="userSpaceOnUse">'
            f'<rect width="60" height="60" fill="{mortar}"/>{"".join(rects)}</pattern>')


def _pattern(kind: str, rng: random.Random, light: bool) -> str:
    if kind == "pierre":
        return _stone_pattern(rng, light)
    if kind == "brique":
        return ('<pattern id="facade" width="40" height="20" patternUnits="userSpaceOnUse">'
                '<rect width="40" height="20" fill="#e2d6c8"/>'
                '<rect x="1" y="1" width="38" height="8" fill="#a9452f"/>'
                '<rect x="-19" y="11" width="38" height="8" fill="#b4513a"/>'
                '<rect x="21" y="11" width="38" height="8" fill="#9c3f2b"/></pattern>')
    if kind == "bois":
        return ('<pattern id="facade" width="24" height="60" patternUnits="userSpaceOnUse">'
                '<rect width="24" height="60" fill="#9a6b41"/><rect width="2" height="60" fill="#6e4a2b"/>'
                '<rect x="12" width="1" height="60" fill="#a77a4f"/></pattern>')
    if kind == "colombages":
        return ('<pattern id="facade" width="80" height="90" patternUnits="userSpaceOnUse">'
                '<rect width="80" height="90" fill="#efe6d2"/>'
                '<g stroke="#4b3322" stroke-width="7"><line x1="0" y1="0" x2="0" y2="90"/>'
                '<line x1="40" y1="0" x2="40" y2="90"/><line x1="0" y1="45" x2="80" y2="45"/>'
                '<line x1="0" y1="45" x2="40" y2="0"/><line x1="40" y1="90" x2="80" y2="45"/></g></pattern>')
    return '<pattern id="facade" width="10" height="10" patternUnits="userSpaceOnUse"><rect width="10" height="10" fill="#f1ece2"/></pattern>'


def _tree(x: float, y: float, s: float, conifer: bool, rng: random.Random) -> str:
    if conifer:
        return (f'<rect x="{x - 4 * s}" y="{y - 10 * s}" width="{8 * s}" height="{20 * s}" fill="#5a3d27"/>'
                f'<polygon points="{x},{y - 120 * s} {x - 38 * s},{y - 10 * s} {x + 38 * s},{y - 10 * s}" fill="#2e5a3a"/>')
    g = rng.choice(["#4f8a3c", "#5d9a45", "#3f7a35", "#6aa04c"])
    return (f'<rect x="{x - 5 * s}" y="{y - 45 * s}" width="{10 * s}" height="{45 * s}" fill="#6b4a2f"/>'
            f'<circle cx="{x}" cy="{y - 70 * s}" r="{36 * s}" fill="{g}"/>'
            f'<circle cx="{x - 22 * s}" cy="{y - 55 * s}" r="{24 * s}" fill="{g}"/>'
            f'<circle cx="{x + 24 * s}" cy="{y - 58 * s}" r="{26 * s}" fill="{g}"/>')


def _background(scene: str, rng: random.Random, horizon: int) -> str:
    parts = []
    if scene == "mer":
        parts.append(f'<rect y="{horizon - 70}" width="{W}" height="70" fill="#3d7fb3"/>'
                     f'<rect y="{horizon - 70}" width="{W}" height="6" fill="#6aa6d1"/>')
    elif scene == "montagne":
        parts.append(f'<polygon points="0,{horizon} 150,{horizon - 230} 320,{horizon} " fill="#7e8ea3"/>'
                     f'<polygon points="200,{horizon} 430,{horizon - 290} 650,{horizon}" fill="#6c7d94"/>'
                     f'<polygon points="430,{horizon - 290} 395,{horizon - 240} 468,{horizon - 240}" fill="#f4f7fb"/>'
                     f'<polygon points="520,{horizon} 700,{horizon - 200} {W},{horizon}" fill="#8796ab"/>')
    elif scene == "ville":
        x = 0
        while x < W:
            w, h = rng.randint(60, 120), rng.randint(90, 200)
            parts.append(f'<rect x="{x}" y="{horizon - h}" width="{w}" height="{h}" fill="{rng.choice(["#c9c3bb", "#b8b2aa", "#d6d0c6"])}"/>')
            for wy in range(horizon - h + 15, horizon - 20, 30):
                for wx in range(x + 10, x + w - 15, 25):
                    parts.append(f'<rect x="{wx}" y="{wy}" width="10" height="14" fill="#8ea4b8"/>')
            x += w + 6
    elif scene == "foret":
        for i in range(18):
            parts.append(_tree(i * 48 + rng.randint(-10, 10), horizon + 5, 0.75, True, rng))
    if scene in ("campagne", "vignes", "foret", "mer", "montagne"):
        parts.append(f'<path d="M0 {horizon} Q 200 {horizon - 40} 400 {horizon - 10} T {W} {horizon - 20} V {horizon + 10} H 0 Z" fill="#9cc47a"/>')
    if scene == "vignes":
        for row in range(6):
            y = horizon - 22 + row * 9
            parts.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y - 10}" stroke="#4d7a2f" stroke-width="4" stroke-dasharray="8 6"/>')
    return "".join(parts)


def _facade(p: dict, rng: random.Random) -> str:
    slope = p.get("slope")
    horizon = 300
    ground = (f'<polygon points="0,{horizon - 20} {W},{horizon + 60} {W},{H} 0,{H}" fill="#86b25e"/>' if slope
              else f'<rect y="{horizon}" width="{W}" height="{H - horizon}" fill="#86b25e"/>')
    trees = "".join(_tree(x, horizon + 140 + rng.randint(-20, 20), rng.uniform(0.8, 1.2), p.get("conifers", False), rng)
                    for x in [70, 720, 140, 660, 30, 760][: min(6, p.get("trees", 2))])
    if p.get("building"):
        house = (f'<rect x="220" y="80" width="360" height="380" fill="url(#facade)" stroke="#a49c90"/>'
                 + "".join(f'<rect x="{x}" y="{y}" width="40" height="64" fill="#5b7088" stroke="#efe9df" stroke-width="5"/>'
                           for x in (250, 330, 410, 490) for y in (110, 210, 310)))
        return ground + trees + house
    big = p.get("big")
    x0, x1 = (170, 630) if big else (230, 570)
    top, bottom = 250, 430
    roof = p.get("roof", "ardoise")
    if roof == "plat":
        roof_svg = f'<rect x="{x0 - 15}" y="{top - 18}" width="{x1 - x0 + 30}" height="18" fill="#3c4148"/>'
    else:
        color = "#4a5561" if roof == "ardoise" else "#c0623b"
        roof_svg = (f'<polygon points="{x0 - 25},{top} {(x0 + x1) / 2},{top - 120} {x1 + 25},{top}" fill="{color}"/>'
                    + "".join(f'<line x1="{x0 - 25 + i * 12}" y1="{top - i * 6}" x2="{x1 + 25 - i * 12}" y2="{top - i * 6}" '
                              f'stroke="#00000022"/>' for i in range(1, 10)))
    if p.get("ruin"):
        roof_svg += f'<polygon points="{(x0 + x1) / 2 - 40},{top - 70} {(x0 + x1) / 2 + 20},{top - 95} {(x0 + x1) / 2 + 30},{top - 40}" fill="#2b2b2b"/>'
    shutter = "#5f8fb0" if p.get("facade") == "pierre" else "#6f8f6a"
    if p.get("ruin"):
        shutter = "#8a7f72"
    windows = "".join(
        f'<rect x="{x}" y="{y}" width="46" height="58" fill="#3f5468" stroke="#e8e2d6" stroke-width="5"/>'
        f'<rect x="{x - 22}" y="{y}" width="20" height="58" fill="{shutter}"/>'
        f'<rect x="{x + 48}" y="{y}" width="20" height="58" fill="{shutter}"/>'
        for x, y in ((x0 + 50, top + 30), (x1 - 100, top + 30)))
    door = f'<rect x="{(x0 + x1) / 2 - 28}" y="{bottom - 100}" width="56" height="100" fill="#6b4630"/>'
    house = f'<rect x="{x0}" y="{top}" width="{x1 - x0}" height="{bottom - top}" fill="url(#facade)"/>'
    extras = ""
    if p.get("pool"):
        extras += '<rect x="420" y="465" width="300" height="70" rx="8" fill="#5cc3e6" stroke="#e9e3d6" stroke-width="8"/>'
    if p.get("terrace"):
        extras += f'<rect x="{x0 - 30}" y="{bottom}" width="{x1 - x0 + 60}" height="22" fill="#b89470"/>'
    if p.get("hedges"):
        extras += f'<rect x="0" y="{horizon + 40}" width="{W}" height="34" rx="16" fill="#3e6b34"/>'
    if p.get("neighbors"):
        extras += ('<rect x="0" y="210" width="200" height="230" fill="#a9452f"/><rect x="600" y="200" width="200" height="240" fill="#9c3f2b"/>')
    return ground + extras + trees + house + roof_svg + windows + door


def _garden(p: dict, rng: random.Random) -> str:
    horizon = 260
    slope = p.get("slope")
    ground = (f'<polygon points="0,{horizon - 30} {W},{horizon + 80} {W},{H} 0,{H}" fill="#8dbb62"/>' if slope
              else f'<rect y="{horizon}" width="{W}" height="{H - horizon}" fill="#8dbb62"/>')
    if slope:
        ground += "".join(f'<line x1="0" y1="{horizon + i * 55}" x2="{W}" y2="{horizon + 110 + i * 55}" stroke="#b9a37f" stroke-width="6"/>'
                          for i in range(1, 5))
    n = p.get("trees", 2)
    trees = "".join(_tree(rng.randint(40, 760), rng.randint(horizon + 80, H - 20), rng.uniform(0.7, 1.4),
                          p.get("conifers", False), rng) for _ in range(min(10, n + 2)))
    extras = ""
    if p.get("orchard"):
        extras += "".join(_tree(120 + i * 140, 470, 0.7, False, rng) + f'<circle cx="{110 + i * 140}" cy="{405}" r="5" fill="#d23b2e"/>'
                          for i in range(5))
    if p.get("pond"):
        extras += '<ellipse cx="420" cy="440" rx="190" ry="55" fill="#4f8fb5"/><ellipse cx="400" cy="430" rx="120" ry="25" fill="#79b2d4"/>'
    if p.get("pool"):
        extras += '<rect x="250" y="400" width="320" height="90" rx="10" fill="#5cc3e6" stroke="#e9e3d6" stroke-width="10"/>'
    if p.get("terrace"):
        extras += '<rect x="0" y="470" width="800" height="90" fill="#b89470"/>' + "".join(
            f'<line x1="{x}" y1="470" x2="{x}" y2="560" stroke="#9c7b5a" stroke-width="3"/>' for x in range(0, 800, 40))
    if p.get("hedges"):
        extras += f'<rect x="0" y="{horizon - 10}" width="{W}" height="60" rx="25" fill="#3e6b34"/>'
    if p.get("neighbors"):
        extras += '<rect x="80" y="110" width="260" height="160" fill="#a9452f"/><rect x="460" y="90" width="280" height="180" fill="#b4513a"/>' \
                  + "".join(f'<rect x="{x}" y="150" width="30" height="40" fill="#3f5468"/>' for x in (120, 200, 280, 500, 580, 660))
    return _background(p.get("scene", "campagne"), rng, horizon) + ground + extras + trees


def _interior(p: dict, rng: random.Random) -> str:
    wall = "#efe7da" if p.get("facade") != "pierre" else "url(#facade)"
    parts = [f'<rect width="{W}" height="400" fill="{wall}"/>']
    floor = "#b07a4a" if p.get("parquet") or p.get("facade") != "enduit" else "#d8d2c8"
    parts.append(f'<rect y="400" width="{W}" height="160" fill="{floor}"/>')
    if floor == "#b07a4a":
        parts += [f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#8d5f37" stroke-width="2"/>' for y in range(420, 560, 22)]
    parts.append('<rect x="480" y="90" width="200" height="230" fill="#bcd8ec" stroke="#ffffff" stroke-width="12"/>'
                 '<line x1="580" y1="90" x2="580" y2="320" stroke="#ffffff" stroke-width="8"/>')
    if p.get("beams"):
        parts += [f'<rect x="0" y="{y}" width="{W}" height="22" fill="#5c3f28"/>' for y in (20, 80)]
        parts += [f'<rect x="{x}" y="0" width="18" height="100" fill="#4e3522"/>' for x in (120, 380, 640)]
    if p.get("fireplace"):
        parts.append('<rect x="90" y="220" width="250" height="40" fill="#cfc4b2"/><rect x="110" y="260" width="210" height="140" fill="#cfc4b2"/>'
                     '<rect x="150" y="290" width="130" height="110" fill="#2c2420"/>'
                     '<path d="M190 400 q 25 -60 20 -80 q 25 30 30 80 Z" fill="#f08a2e"/>')
    parts.append('<rect x="380" y="430" width="300" height="70" rx="12" fill="#6d7f8f"/>')
    return "".join(parts)


def render(params: dict, view: int, seed: str) -> str:
    rng = random.Random(f"{seed}-{view}")
    pattern = _pattern(params.get("facade", "enduit"), rng, params.get("stone_light", False))
    sky = ('<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9fc9ea"/>'
           f'<stop offset="1" stop-color="#e4f1fa"/></linearGradient>{pattern}</defs>')
    body = '<rect width="800" height="560" fill="url(#sky)"/>'
    if view == 0:
        body += _background(params.get("scene", "campagne"), rng, 300) + _facade(params, rng)
    elif view == 1:
        body += _garden(params, rng)
    else:
        body += _interior(params, rng)
    caption = escape("Illustration de démonstration — " + ["façade", "extérieur", "intérieur"][min(view, 2)])
    width = len(caption) * 7.2 + 20
    body += (f'<rect x="{W - width - 12}" y="{H - 40}" width="{width}" height="28" rx="6" fill="#000000aa"/>'
             f'<text x="{W - width - 2}" y="{H - 21}" font-family="sans-serif" font-size="14" fill="#fff">{caption}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{sky}{body}</svg>'
