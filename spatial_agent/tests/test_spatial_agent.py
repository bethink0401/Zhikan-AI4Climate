from __future__ import annotations

import json
from pathlib import Path

from spatial_agent.agents.spatial_agent import SpatialAgent
from spatial_agent.tools.spatial_tools import angular_difference, haversine_km


def _write_fixture(path: Path, features: list[dict]) -> Path:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return path


def test_basic_geometry() -> None:
    assert angular_difference(350, 10) == 20
    assert 110 < haversine_km(114, 22, 114, 23) < 112


def test_mong_kok_directional_match(tmp_path: Path) -> None:
    roads = _write_fixture(
        tmp_path / "roads.geojson",
        [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[114.1683, 22.327], [114.17, 22.327]]},
                "properties": {"road_id": "north", "road_name": "North Road"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[114.1683, 22.318], [114.17, 22.318]]},
                "properties": {"road_id": "south", "road_name": "South Road"},
            },
        ],
    )
    pois = _write_fixture(
        tmp_path / "pois.geojson",
        [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [114.1683, 22.318]},
                "properties": {"poi_id": "school-1", "poi_type": "school", "name": "Test School"},
            }
        ],
    )
    result = SpatialAgent(roads, pois).run(
        {"task_id": "test-1", "station_id": "MK"},
        {"result": {"wind_direction": 0, "wind_speed": 2.0}},
    )
    assert result["status"] == "success"
    assert result["result"]["upwind_roads"][0]["road_id"] == "north"
    assert result["result"]["sensitive_poi_count"] == 1


def test_missing_weather_degrades_without_crashing(tmp_path: Path) -> None:
    empty = _write_fixture(tmp_path / "empty.geojson", [])
    result = SpatialAgent(empty, empty).run({"station_id": "CB"}, None)
    assert result["status"] == "warning"
    assert result["result"]["affected_area_geojson"]["type"] == "Polygon"
    assert result["data_quality"]["missing_rate"] > 0
