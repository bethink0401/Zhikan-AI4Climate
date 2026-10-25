"""Build a message-local map snapshot from trusted propagation results only."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any


def propagation_map_payload(canonical: Mapping[str, Any] | None) -> dict[str, Any] | None:
    agents = (canonical or {}).get("agent_results") or {}
    weather = (agents.get("MeteorologyAgent") or {}).get("result") or {}
    spatial = (agents.get("SpatialAgent") or {}).get("result") or {}
    prediction = weather.get("pollution_path_prediction") or {}
    geometry = prediction.get("propagation_sector_geojson") or {}
    path = prediction.get("path_geojson") or {}
    coordinates = path.get("coordinates") or []
    if (prediction.get("status") != "ok" or geometry.get("type") not in {"Polygon", "MultiPolygon"}
            or not geometry.get("coordinates") or path.get("type") != "LineString" or len(coordinates) < 2):
        return None
    if any(not isinstance(point, (list, tuple)) or len(point) < 2
           or any(not isinstance(v, (int, float)) or not isfinite(v) for v in point[:2]) for point in coordinates):
        return None
    task = (canonical or {}).get("task") or {}
    station = deepcopy(spatial.get("station") or {})
    if not station:
        station = {key: task[key] for key in ("station_id", "station_name", "lat", "lon") if key in task}
        station.update(aliases=[], station_type="general", address="")
    if any(not isinstance(station.get(key), (int, float)) or not isfinite(station[key]) for key in ("lon", "lat")):
        return None
    # A stale/mismatched source must not overlay a different station's forecast.
    if abs(station["lon"] - coordinates[0][0]) > .0001 or abs(station["lat"] - coordinates[0][1]) > .0001:
        return None
    # Omit road polylines and legacy static scopes. These are not part of this map.
    map_spatial = {"station": station, "propagation_exposure": spatial.get("propagation_exposure"),
                   "upwind_roads": [], "nearby_road_count": 0, "nearby_road_segment_count": 0,
                   "sensitive_pois": [], "sensitive_poi_count": 0, "sensitive_poi_by_type": {},
                   "analysis_radius_km": spatial.get("analysis_radius_km", 3),
                   "spatial_confidence": spatial.get("spatial_confidence", "low")}
    return deepcopy({"title": "污染传播模拟地图", "spatial": map_spatial, "meteorology": weather,
                     "station_name": station.get("station_name") or task.get("station_name") or "",
                     "start_time": prediction.get("simulation_start_time") or task.get("start_time"),
                     "end_time": prediction.get("requested_end_time") or task.get("end_time")})
