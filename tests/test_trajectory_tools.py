"""Regression tests for the short-term wind and pollution-path algorithms."""

from __future__ import annotations

import pandas as pd

from tools.trajectory_tools import forecast_wind_vectors, predict_pollution_path


def test_forecast_respects_circular_north_boundary() -> None:
    hourly = pd.DataFrame({
        "wind_direction": [350.0, 355.0, 2.0, 8.0],
        "wind_speed": [2.0, 2.0, 2.0, 2.0],
    })
    result = forecast_wind_vectors(hourly, horizon_hours=2)
    assert result["status"] == "ok"
    first = result["forecast_hours"][0]
    assert min(first["wind_from_direction"], 360 - first["wind_from_direction"]) < 20
    assert 0 <= first["direction_uncertainty_deg"] <= 180


def test_advection_path_uses_wind_to_direction() -> None:
    # A north wind comes from north and transports the parcel south.
    hourly = pd.DataFrame({"wind_direction": [0.0, 0.0, 0.0], "wind_speed": [2.0, 2.0, 2.0]})
    forecast = forecast_wind_vectors(hourly, horizon_hours=1)
    path = predict_pollution_path(114.1683, 22.3227, forecast)
    assert path["status"] == "ok"
    assert path["steps"][0]["lat"] < 22.3227
    assert path["path_geojson"]["type"] == "LineString"
    assert path["uncertainty_corridor_geojson"]["type"] == "Polygon"


def test_calm_or_missing_wind_does_not_emit_path() -> None:
    hourly = pd.DataFrame({"wind_direction": [90.0, 90.0], "wind_speed": [0.1, 0.1]})
    forecast = forecast_wind_vectors(hourly)
    path = predict_pollution_path(114.0, 22.0, forecast)
    assert forecast["status"] == "calm"
    assert path["status"] == "unavailable"
    assert path["path_geojson"] is None
