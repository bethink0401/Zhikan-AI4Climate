"""Dependency-free geospatial calculations used by the Spatial Agent.

The processed datasets are WGS84 GeoJSON. Calculations use a local equirectangular
projection, which is sufficiently accurate for the required 1--3 km analysis.
Meteorological wind direction is interpreted as the direction the wind comes from.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

EARTH_RADIUS_KM = 6371.0088


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def angular_difference(a: float, b: float) -> float:
    """Return the smallest unsigned difference between two bearings."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial bearing clockwise from true north."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(lon: float, lat: float, distance_km: float, bearing: float) -> list[float]:
    delta = distance_km / EARTH_RADIUS_KM
    theta = math.radians(bearing)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta)
        + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return [math.degrees(lambda2), math.degrees(phi2)]


def _local_xy(lon: float, lat: float, ref_lon: float, ref_lat: float) -> tuple[float, float]:
    """Convert WGS84 to local kilometres around a reference point."""
    x = math.radians(lon - ref_lon) * EARTH_RADIUS_KM * math.cos(math.radians(ref_lat))
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_KM
    return x, y


def nearest_point_on_line(
    station_lon: float, station_lat: float, coordinates: list[list[float]]
) -> tuple[float, float, float]:
    """Return (lon, lat, distance_km) of the nearest point on a polyline."""
    if not coordinates:
        raise ValueError("Road geometry has no coordinates")
    if len(coordinates) == 1:
        lon, lat = coordinates[0][:2]
        return lon, lat, haversine_km(station_lon, station_lat, lon, lat)

    best: tuple[float, float, float] | None = None
    for first, second in zip(coordinates, coordinates[1:]):
        ax, ay = _local_xy(first[0], first[1], station_lon, station_lat)
        bx, by = _local_xy(second[0], second[1], station_lon, station_lat)
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else clamp(-(ax * dx + ay * dy) / denom, 0.0, 1.0)
        x, y = ax + t * dx, ay + t * dy
        distance = math.hypot(x, y)
        lon = first[0] + t * (second[0] - first[0])
        lat = first[1] + t * (second[1] - first[1])
        if best is None or distance < best[2]:
            best = (lon, lat, distance)
    assert best is not None
    return best


def make_circle_polygon(lon: float, lat: float, radius_km: float, steps: int = 72) -> dict[str, Any]:
    ring = [destination_point(lon, lat, radius_km, i * 360.0 / steps) for i in range(steps)]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def make_downwind_sector(
    lon: float,
    lat: float,
    wind_from_deg: float,
    inner_km: float = 0.0,
    outer_km: float = 3.0,
    half_angle_deg: float = 45.0,
    steps: int = 24,
) -> dict[str, Any]:
    """Build a sector pointing downwind (opposite the wind-from direction)."""
    centre = (wind_from_deg + 180.0) % 360.0
    start, end = centre - half_angle_deg, centre + half_angle_deg
    outer = [
        destination_point(lon, lat, outer_km, start + (end - start) * i / steps)
        for i in range(steps + 1)
    ]
    if inner_km <= 0:
        ring = [[lon, lat], *outer, [lon, lat]]
    else:
        inner = [
            destination_point(lon, lat, inner_km, end - (end - start) * i / steps)
            for i in range(steps + 1)
        ]
        ring = [*outer, *inner, outer[0]]
    return {"type": "Polygon", "coordinates": [ring]}


def _iter_line_coordinates(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    if geometry.get("type") == "LineString":
        yield geometry.get("coordinates", [])
    elif geometry.get("type") == "MultiLineString":
        yield from geometry.get("coordinates", [])


def _point_is_downwind(
    station_lon: float,
    station_lat: float,
    point_lon: float,
    point_lat: float,
    wind_from_deg: float | None,
    radius_km: float,
    half_angle_deg: float,
) -> tuple[bool, float, float]:
    distance = haversine_km(station_lon, station_lat, point_lon, point_lat)
    point_bearing = bearing_deg(station_lon, station_lat, point_lon, point_lat)
    if distance > radius_km:
        return False, distance, point_bearing
    if wind_from_deg is None:
        return True, distance, point_bearing
    downwind = (wind_from_deg + 180.0) % 360.0
    return angular_difference(point_bearing, downwind) <= half_angle_deg, distance, point_bearing


def analyze_spatial_context(
    *,
    station: dict[str, Any],
    wind_direction: float | None,
    wind_speed: float | None,
    roads: dict[str, Any],
    pois: dict[str, Any],
    radius_km: float = 3.0,
    upwind_half_angle_deg: float = 60.0,
    downwind_half_angle_deg: float = 45.0,
    max_upwind_roads: int = 15,
) -> dict[str, Any]:
    """Match roads and sensitive POIs to a station and meteorological wind."""
    station_lon, station_lat = float(station["lon"]), float(station["lat"])
    wind_valid = wind_direction is not None and math.isfinite(float(wind_direction))
    wind_from = float(wind_direction) % 360.0 if wind_valid else None
    directional_wind = (
        wind_from
        if wind_from is not None and wind_speed is not None and float(wind_speed) >= 0.5
        else None
    )

    road_candidates: list[dict[str, Any]] = []
    nearby_road_segment_count = 0
    nearby_road_keys: set[str] = set()
    for feature in roads.get("features", []):
        best: tuple[float, float, float] | None = None
        for line in _iter_line_coordinates(feature.get("geometry") or {}):
            if not line:
                continue
            candidate = nearest_point_on_line(station_lon, station_lat, line)
            if best is None or candidate[2] < best[2]:
                best = candidate
        if best is None or best[2] > radius_km:
            continue
        nearby_road_segment_count += 1
        nearest_lon, nearest_lat, distance = best
        props = feature.get("properties", {})
        road_id = str(props.get("road_id") or props.get("object_id") or "")
        road_name = props.get("road_name") or props.get("road_name_zh") or "Unnamed road"
        street_code = str(props.get("street_code") or "")
        unique_key = street_code or (str(road_name).casefold() if road_name != "Unnamed road" else road_id)
        nearby_road_keys.add(unique_key)
        direction = bearing_deg(station_lon, station_lat, nearest_lon, nearest_lat)
        difference = angular_difference(direction, directional_wind) if directional_wind is not None else 180.0
        alignment = max(0.0, math.cos(math.radians(difference))) if directional_wind is not None else 0.0
        if directional_wind is None or difference > upwind_half_angle_deg:
            continue
        distance_score = max(0.0, 1.0 - distance / radius_km)
        score = 0.75 * alignment + 0.25 * distance_score
        road_candidates.append(
            {
                "road_id": road_id,
                "street_code": street_code,
                "road_name": road_name,
                "road_name_zh": props.get("road_name_zh") or "",
                "distance_to_station_km": round(distance, 3),
                "bearing_from_station": round(direction, 1),
                "wind_angle_difference": round(difference, 1),
                "upwind_alignment": round(alignment, 3),
                "match_score": round(score, 3),
                "geometry": feature.get("geometry"),
            }
        )
    road_candidates.sort(key=lambda item: (-item["match_score"], item["distance_to_station_km"]))
    upwind_roads: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    for road in road_candidates:
        key = road["street_code"] or str(road["road_name"]).casefold() or road["road_id"]
        if key in selected_keys:
            continue
        selected_keys.add(key)
        upwind_roads.append(road)
        if len(upwind_roads) >= max_upwind_roads:
            break

    exposed_pois: list[dict[str, Any]] = []
    for feature in pois.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point" or not geometry.get("coordinates"):
            continue
        point_lon, point_lat = geometry["coordinates"][:2]
        inside, distance, direction = _point_is_downwind(
            station_lon,
            station_lat,
            point_lon,
            point_lat,
            directional_wind,
            radius_km,
            downwind_half_angle_deg,
        )
        if not inside:
            continue
        props = feature.get("properties", {})
        exposed_pois.append(
            {
                "poi_id": str(props.get("poi_id") or ""),
                "poi_type": props.get("poi_type") or "other",
                "name": props.get("name") or props.get("name_en") or props.get("name_zh") or "Unnamed POI",
                "name_zh": props.get("name_zh") or "",
                "distance_to_station_km": round(distance, 3),
                "bearing_from_station": round(direction, 1),
                "lon": point_lon,
                "lat": point_lat,
            }
        )
    exposed_pois.sort(key=lambda item: item["distance_to_station_km"])

    type_counts: dict[str, int] = {}
    for poi in exposed_pois:
        type_counts[poi["poi_type"]] = type_counts.get(poi["poi_type"], 0) + 1

    calm_or_missing = directional_wind is None
    if calm_or_missing:
        affected_geometry = make_circle_polygon(station_lon, station_lat, radius_km)
        affected_area = f"station buffer within {radius_km:g} km (wind direction unavailable or unreliable)"
    else:
        affected_geometry = make_downwind_sector(
            station_lon,
            station_lat,
            wind_from,
            outer_km=radius_km,
            half_angle_deg=downwind_half_angle_deg,
        )
        affected_area = f"downwind sector within {radius_km:g} km"

    return {
        "upwind_roads": upwind_roads,
        "nearby_road_count": len(nearby_road_keys),
        "nearby_road_segment_count": nearby_road_segment_count,
        "affected_area": affected_area,
        "affected_area_geojson": affected_geometry,
        "sensitive_poi_count": len(exposed_pois),
        "sensitive_poi_by_type": type_counts,
        "sensitive_pois": exposed_pois,
        "wind_direction_interpreted_as": "meteorological wind-from bearing clockwise from true north",
    }


def load_geojson(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def analyze_forecast_exposure(station: dict[str, Any], pois: dict[str, Any],
                              prediction: dict[str, Any]) -> dict[str, Any]:
    """Screen the full prepared POI inventory against the trajectory's exact union.

    This separate scope does not replace the static 3 km source/governance scope.
    Spatial coverage alone is not observed pollution or a health-risk grade.
    """
    from shapely.geometry import Point, shape
    from shapely.prepared import prep

    geometry = prediction.get("propagation_sector_geojson")
    scope: dict[str, Any] = {
        "status": "unavailable", "geometry_source": "propagation_sector_geojson",
        "sensitive_pois": [], "sensitive_poi_count": 0, "sensitive_poi_by_type": {},
        "note": "按所选时段的模型传播范围筛选全量敏感设施；空间覆盖不代表已观测到污染或健康风险。",
    }
    if prediction.get("status") != "ok" or not geometry:
        return scope
    covered_area = prep(shape(geometry))
    included = []
    seen = set()
    for feature in pois.get("features", []):
        point = feature.get("geometry") or {}
        coordinates = point.get("coordinates") or []
        if point.get("type") != "Point" or len(coordinates) < 2:
            continue
        lon, lat = coordinates[:2]
        if not covered_area.covers(Point(lon, lat)):
            continue
        props = feature.get("properties") or {}
        key = (props.get("poi_id"), lon, lat)
        if key in seen:
            continue
        seen.add(key)
        included.append({
            "poi_id": str(props.get("poi_id") or f"forecast-{len(included)}"),
            "poi_type": props.get("poi_type") or "other",
            "name": props.get("name") or props.get("name_en") or props.get("name_zh") or "Unnamed POI",
            "name_zh": props.get("name_zh") or "", "lon": lon, "lat": lat,
            "distance_to_station_km": round(haversine_km(station["lon"], station["lat"], lon, lat), 3),
            "bearing_from_station": round(bearing_deg(station["lon"], station["lat"], lon, lat), 1),
        })
    included.sort(key=lambda item: item["distance_to_station_km"])
    counts: dict[str, int] = {}
    for poi in included:
        counts[poi["poi_type"]] = counts.get(poi["poi_type"], 0) + 1
    scope.update(status="ok", sensitive_pois=included,
                 sensitive_poi_count=len(included), sensitive_poi_by_type=counts)
    return scope


def analyze_directional_exposure(station: dict[str, Any], pois: dict[str, Any],
                                 prediction: dict[str, Any], radius_km: float) -> dict[str, Any]:
    """Rotate the original static 90-degree sector; keep its original radius.

    Match the first moving trajectory segment used by the propagation map arrow.
    This is a reference sector, not the animated forecast envelope.
    """
    coordinates = (prediction.get("path_geojson") or {}).get("coordinates") or []
    direction = None
    if prediction.get("status") == "ok":
        for start, end in zip(coordinates, coordinates[1:]):
            if len(start) < 2 or len(end) < 2 or not all(math.isfinite(v) for v in [*start[:2], *end[:2]]):
                continue
            if abs(end[0] - start[0]) + abs(end[1] - start[1]) < 1e-9:
                continue
            direction = bearing_deg(*start[:2], *end[:2])
            break
    geometry = make_downwind_sector(station["lon"], station["lat"], (direction + 180) % 360,
                                    outer_km=radius_km, half_angle_deg=45) if direction is not None else None
    scope = analyze_forecast_exposure(station, pois, {
        "status": "ok" if geometry else "unavailable", "propagation_sector_geojson": geometry,
    })
    scope.update(geometry=geometry, direction=direction, radius_km=radius_km,
                 geometry_source="path_initial_direction_static_radius",
                 note="原半径静态参考扇区，仅按模拟路径起始方向旋转；不是逐小时模拟传播范围或实测污染。")
    return scope
