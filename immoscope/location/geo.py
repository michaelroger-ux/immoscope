"""Calculs de distances."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Projection équirectangulaire locale (suffisante à l'échelle de quelques km)."""
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def distance_to_polyline_m(lat: float, lon: float, points: list[tuple[float, float]]) -> tuple[float, tuple[float, float]]:
    """Distance minimale d'un point à une polyligne [(lat, lon), …] et point le plus proche."""
    if not points:
        return float("inf"), (lat, lon)
    if len(points) == 1:
        return haversine_m(lat, lon, *points[0]), points[0]
    best = float("inf")
    best_pt = points[0]
    for (la1, lo1), (la2, lo2) in zip(points, points[1:]):
        x1, y1 = _to_xy(la1, lo1, lat, lon)
        x2, y2 = _to_xy(la2, lo2, lat, lon)
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg2))
        px, py = x1 + t * dx, y1 + t * dy
        d = math.hypot(px, py)
        if d < best:
            best = d
            best_pt = (la1 + t * (la2 - la1), lo1 + t * (lo2 - lo1))
    return best, best_pt
