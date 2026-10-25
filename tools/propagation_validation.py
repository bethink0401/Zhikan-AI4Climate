"""Held-out monitoring-station checks after a trajectory has been computed.

Crossing/covering a station is geometry, never proof of forecast accuracy.
Observations here are not fed back into wind, parcel life, or parameters.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from shapely.geometry import Point, shape

from tools.parcel_lifetime import finite_number, load_parcel_rules


def station_key(name: str) -> str:
    return name.upper().replace(" ROADSIDE", "").replace("_", " ").strip()


def _closest_on_path(lon: float, lat: float, coordinates: list[list[float]]) -> tuple[float, float]:
    best_distance, best_lead = float("inf"), 0.0
    for index, (start, end) in enumerate(zip(coordinates, coordinates[1:])):
        scale_x = math.pi / 180 * 6371.0088 * math.cos(math.radians(lat))
        scale_y = math.pi / 180 * 6371.0088
        ax, ay = (start[0] - lon) * scale_x, (start[1] - lat) * scale_y
        dx, dy = (end[0] - start[0]) * scale_x, (end[1] - start[1]) * scale_y
        length = dx * dx + dy * dy
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length)) if length else 0.0
        distance = math.hypot(ax + t * dx, ay + t * dy)
        if distance < best_distance:
            best_distance, best_lead = distance, index + t
    return best_distance, best_lead


def find_station_encounters(prediction: dict[str, Any], stations: list[dict[str, Any]],
                            source_name: str, start_time: str) -> list[dict[str, Any]]:
    coordinates = (prediction.get("path_geojson") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return []
    config = load_parcel_rules()["station_validation"]
    sector_data = prediction.get("propagation_sector_geojson")
    sector = shape(sector_data) if sector_data else None
    stages = [(item["lead_hour"], shape(item["geometry"])) for item in prediction.get("sector_steps") or []]
    origin = datetime.fromisoformat(start_time)
    encounters = []
    for station in stations:
        if station_key(str(station.get("station_name") or "")) == station_key(source_name):
            continue
        lon, lat = finite_number(station.get("lon")), finite_number(station.get("lat"))
        if lon is None or lat is None:
            continue
        distance, lead = _closest_on_path(lon, lat, coordinates)
        near_path = distance <= config["centerline_radius_km"] and lead > 0.01
        point = Point(lon, lat)
        covered = bool(sector is not None and sector.covers(point))
        if not covered:
            continue
        if not near_path:
            lead = float(next((hour for hour, geometry in stages if geometry.covers(point)), len(coordinates) - 1))
        arrival = origin + timedelta(hours=lead)
        encounters.append({
            "station_id": str(station.get("station_id") or station.get("station_name")),
            "station_name": station.get("station_name"),
            "station_name_zh": station.get("station_name_zh") or station.get("station_name"),
            "lon": lon, "lat": lat,
            "relation": "centerline_near" if near_path else "sector_coverage",
            "relation_label": "中心路径邻近" if near_path else "仅扇区覆盖",
            "distance_to_path_km": round(distance, 3),
            "arrival_lead_hour": round(lead, 3),
            "predicted_arrival_time": arrival.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return sorted(encounters, key=lambda item: (item["arrival_lead_hour"], item["distance_to_path_km"]))


def evaluate_station_observations(encounters: list[dict[str, Any]], observations: dict[str, list[dict[str, Any]]],
                                  initial_conditions: dict[str, Any], start_time: str, end_time: str) -> dict[str, Any]:
    config = load_parcel_rules()["station_validation"]
    start, end = datetime.fromisoformat(start_time), datetime.fromisoformat(end_time)
    threshold = float(config["relative_increase_threshold"])
    source = initial_conditions.get("pollutants") or {}
    output = []
    labels = {"consistent": "观测变化支持", "not_supported": "未观察到对应升高",
              "mixed": "不同污染物结果不一致", "pre_existing": "到达前已升高，无法归因",
              "not_testable": "起点污染信号不足", "unavailable": "观测数据不足",
              "coverage_only": "仅空间覆盖，未判定一致性"}
    for encounter in encounters:
        item = dict(encounter)
        rows = observations.get(station_key(str(item["station_name"])), [])
        arrival = datetime.fromisoformat(item["predicted_arrival_time"])
        sample_start = arrival.replace(minute=0, second=0, microsecond=0)
        if sample_start < arrival:
            sample_start += timedelta(hours=1)
        sample_end = min(end, sample_start + timedelta(hours=config["arrival_window_hours"]))
        comparisons = []
        for pollutant, initial in source.items():
            c0 = finite_number(initial.get("initial_concentration"))
            cref = finite_number(initial.get("reference_concentration"))
            if c0 is None or cref is None or cref <= 0 or c0 <= cref * (1 + threshold):
                continue
            baseline, observed, before_arrival = [], [], []
            for row in rows:
                value = finite_number(row.get(pollutant))
                if value is None or value < 0:
                    continue
                stamp = datetime.fromisoformat(str(row["time"]))
                if start - timedelta(hours=config["baseline_hours"]) <= stamp < start:
                    baseline.append(value)
                if sample_start <= stamp <= end and stamp < sample_start + timedelta(hours=config["arrival_window_hours"]):
                    observed.append((stamp, value))
                if start <= stamp < arrival:
                    before_arrival.append(value)
            reference = float(median(baseline)) if len(baseline) >= config["minimum_baseline_samples"] else None
            measured = float(median([value for _, value in observed])) if observed else None
            status = "unavailable"
            if reference is not None and reference > 0 and measured is not None:
                if any(value > reference * (1 + threshold) for value in before_arrival):
                    status = "pre_existing"
                else:
                    status = "consistent" if measured > reference * (1 + threshold) else "not_supported"
            comparisons.append({"pollutant": pollutant, "unit": initial["unit"],
                "source_initial": c0, "source_reference": cref,
                "baseline_concentration": reference, "baseline_samples": len(baseline),
                "arrival_observed_concentration": measured,
                "observation_times": [stamp.strftime("%Y-%m-%d %H:%M:%S") for stamp, _ in observed],
                "change_percent": round((measured / reference - 1) * 100, 2)
                    if measured is not None and reference is not None and reference > 0 else None,
                "status": status, "label": labels[status]})
        statuses = {comparison["status"] for comparison in comparisons}
        if item["relation"] == "sector_coverage":
            status = "coverage_only"
        elif "consistent" in statuses and "not_supported" in statuses:
            status = "mixed"
        elif "consistent" in statuses:
            status = "consistent"
        elif "not_supported" in statuses:
            status = "not_supported"
        elif "pre_existing" in statuses:
            status = "pre_existing"
        else:
            status = "unavailable" if comparisons else "not_testable"
        item.update(status=status, label=labels[status], comparisons=comparisons,
                    observation_window_start=sample_start.strftime("%Y-%m-%d %H:%M:%S"),
                    observation_window_end=sample_end.strftime("%Y-%m-%d %H:%M:%S"))
        output.append(item)
    return {"stations": output, "centerline_radius_km": config["centerline_radius_km"],
            "relative_increase_threshold": threshold,
            "independent_of_forecast": True,
            "summary": {"encounter_count": len(output),
                "centerline_count": sum(item["relation"] == "centerline_near" for item in output),
                "supported_count": sum(item["status"] == "consistent" for item in output),
                "not_supported_count": sum(item["status"] == "not_supported" for item in output),
                "mixed_count": sum(item["status"] == "mixed" for item in output)},
            "note": "到达站点的观测未参与预测或参数计算。仅比较到达后是否出现与起点污染信号同向的升高；"
                    "到达前已升高、数据缺失或仅扇区覆盖时不判为支持。空间经过和观测一致均不能单独证明传播因果或预测准确。"}


def attach_station_validation(prediction: dict[str, Any], task: dict[str, Any],
                              initial_conditions: dict[str, Any], db_path: Path | None = None) -> None:
    from api.stations import epd_stations
    from tools import air_quality_tools

    encounters = find_station_encounters(prediction, epd_stations(), str(task.get("station_name") or ""), task["start_time"])
    rows_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    error = None
    if encounters:
        start = datetime.fromisoformat(task["start_time"]) - timedelta(hours=load_parcel_rules()["station_validation"]["baseline_hours"])
        try:
            conn = air_quality_tools.open_db(db_path or air_quality_tools.DEFAULT_DB)
            try:
                rows = conn.execute("SELECT station_name, time, no2, pm25, pm10, o3, so2, co "
                                    "FROM air_quality_hourly WHERE time >= ? AND time <= ? ORDER BY time",
                                    (start.strftime("%Y-%m-%d %H:%M:%S"), task["end_time"])).fetchall()
            finally:
                conn.close()
            for row in rows:
                value = dict(row)
                rows_by_station[station_key(value["station_name"])].append(value)
        except (sqlite3.Error, FileNotFoundError) as exc:
            error = type(exc).__name__
    validation = evaluate_station_observations(encounters, rows_by_station, initial_conditions, task["start_time"], task["end_time"])
    if error:
        validation["data_status"] = "unavailable"
        validation["data_reason"] = "未能读取独立监测数据，站点标记仅表示空间覆盖"
    prediction["station_validation"] = validation
