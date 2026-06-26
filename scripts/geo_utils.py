#!/usr/bin/env python3
"""Small geo helpers shared by the build scripts (copied from the BiCRS engine_core)."""
import math


def haversine_km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km between two (lon, lat) points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def point_in_ring(lon, lat, ring):
    """Ray-casting point-in-polygon for one ring of [lon, lat] coords."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, geometry):
    """True if (lon,lat) is inside a GeoJSON Polygon/MultiPolygon geometry.
    First ring of each polygon is the outer boundary (holes ignored — fine for storage basins)."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        return point_in_ring(lon, lat, coords[0]) if coords else False
    if gtype == "MultiPolygon":
        return any(poly and point_in_ring(lon, lat, poly[0]) for poly in coords)
    return False


def nearest_point_on_ring_km(lon, lat, ring):
    """Min great-circle distance (km) and nearest vertex [lat,lon] from (lon,lat) to a ring's
    vertices. Vertex-level is enough for screening; densify the ring beforehand for finer edges."""
    best_km, best_pt = float("inf"), None
    for x, y in ring:
        d = haversine_km(lon, lat, x, y)
        if d < best_km:
            best_km, best_pt = d, [y, x]
    return best_km, best_pt
