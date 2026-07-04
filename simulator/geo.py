"""Great-circle geo helpers for moving simulated units along their routes."""

from __future__ import annotations

import math

_R = 6_371_000.0  # mean Earth radius (m)

Point = tuple[float, float]  # (lat, lon) in degrees


def haversine(a: Point, b: Point) -> float:
    """Distance in metres between two lat/lon points."""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _R * math.asin(min(1.0, math.sqrt(h)))


def bearing(a: Point, b: Point) -> float:
    """Initial bearing (degrees true, 0-360) from a to b."""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def dest_point(a: Point, bearing_deg: float, dist_m: float) -> Point:
    """The point ``dist_m`` metres from ``a`` along ``bearing_deg``."""
    lat1, lon1 = a
    br = math.radians(bearing_deg)
    d = dist_m / _R
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return (math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0)
