"""Benchmark: MeteorologyAgent (dispersion grading + wind handling).

Measures:
  - dispersion condition distribution and weather-risk spread across
    8 windows spread over 2023 at the Mong Kok roadside station
  - downwind = (upwind + 180) % 360 consistency on every run
  - vector vs scalar wind averaging on the 3 roadside stations of the
    event window (scalar averaging is the naive alternative)
  - per-run latency

Usage: /usr/bin/python3 tests/benchmarks/bench_meteorology.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EVENT_WINDOW, ROADSIDE_STATIONS, Timer, describe, save_result

from agents import meteorology_agent as met


WINDOWS = [
    ("2023-02-13 08:00:00", "2023-02-13 11:00:00"),
    ("2023-04-11 08:00:00", "2023-04-11 11:00:00"),
    ("2023-05-25 08:00:00", "2023-05-25 11:00:00"),
    ("2023-07-06 08:00:00", "2023-07-06 11:00:00"),
    ("2023-08-15 08:00:00", "2023-08-15 11:00:00"),  # event
    ("2023-09-21 08:00:00", "2023-09-21 11:00:00"),
    ("2023-11-20 08:00:00", "2023-11-20 11:00:00"),
    ("2023-12-14 08:00:00", "2023-12-14 11:00:00"),
]


def scalar_mean_direction(degrees: list[float]) -> float | None:
    """Arithmetic mean of compass angles — the naive approach."""
    if not degrees:
        return None
    return sum(degrees) / len(degrees)


def vector_mean_direction(degrees: list[float]) -> float | None:
    """Vector mean of 'wind from' angles (movement vector = from + 180)."""
    if not degrees:
        return None
    x = sum(math.sin(math.radians(d + 180.0)) for d in degrees)
    y = sum(math.cos(math.radians(d + 180.0)) for d in degrees)
    if x == 0 and y == 0:
        return None
    to_dir = math.degrees(math.atan2(x, y)) % 360.0
    return (to_dir + 180.0) % 360.0


def angular_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def main() -> int:
    mk = ROADSIDE_STATIONS[0]
    timer = Timer()
    payload: dict = {"agent": "MeteorologyAgent"}

    # -- dispersion grading across seasons --------------------------------
    runs = []
    for start, end in WINDOWS:
        task = {"task_id": f"BENCH_MET_{start[:10]}",
                "station_id": mk["station_id"],
                "lat": mk["lat"], "lon": mk["lon"],
                "start_time": start, "end_time": end}
        out = timer.wrap(met.run, task)
        r = out.get("result") or {}
        up, down = r.get("dominant_wind_direction"), r.get("downwind_direction")
        consistent = (up is None and down is None) or (
            up is not None and down is not None
            and angular_diff(float(down), (float(up) + 180.0) % 360.0) < 0.5)
        runs.append({
            "date": start[:10],
            "status": out.get("status"),
            "weather_risk": r.get("weather_risk"),
            "dispersion_condition": r.get("dispersion_condition"),
            "mean_wind_speed": r.get("mean_wind_speed"),
            "downwind_consistent": consistent,
        })
    risks = [r["weather_risk"] for r in runs if r["weather_risk"] is not None]
    payload["dispersion"] = {
        "windows": runs,
        "weather_risk_stats": describe(risks),
        "condition_distribution": {
            k: sum(1 for r in runs if r["dispersion_condition"] == k)
            for k in {r["dispersion_condition"] for r in runs}
        },
        "downwind_consistency": f"{sum(1 for r in runs if r['downwind_consistent'])}/{len(runs)}",
    }

    # -- wind averaging: vector vs scalar on the event window --------------
    per_station = []
    for st in ROADSIDE_STATIONS:
        task = {"task_id": f"BENCH_WIND_{st['station_id']}",
                "station_id": st["station_id"],
                "lat": st["lat"], "lon": st["lon"],
                "start_time": EVENT_WINDOW[0], "end_time": EVENT_WINDOW[1]}
        out = timer.wrap(met.run, task)
        r = out.get("result") or {}
        per_station.append({
            "station": st["station_name"],
            "direction": r.get("dominant_wind_direction"),
            "speed": r.get("mean_wind_speed"),
        })
    dirs = [p["direction"] for p in per_station if p["direction"] is not None]
    vec = vector_mean_direction(dirs)
    sca = scalar_mean_direction(dirs)
    payload["wind_averaging"] = {
        "stations": per_station,
        "vector_mean_direction": None if vec is None else round(vec, 1),
        "scalar_mean_direction": None if sca is None else round(sca, 1),
        "angular_error_of_scalar": None if (vec is None or sca is None)
            else round(angular_diff(vec, sca), 1),
        "synthetic_edge_case": {
            "input_directions": [350.0, 10.0],
            "vector_mean": vector_mean_direction([350.0, 10.0]),
            "scalar_mean": scalar_mean_direction([350.0, 10.0]),
            "note": "北北西 350° 与 北北东 10° 的算术平均得到 180°（南风），"
                    "方向完全反转；矢量平均正确得到 0°（北风）",
        },
        "note": "scalar averaging of compass angles is the naive baseline; "
                "the API layer uses vector averaging (api/travel_api.py)",
    }
    payload["latency_ms"] = timer.summary

    path = save_result("meteorology", payload)
    print(f"saved -> {path}")
    print(f"weather_risk: {payload['dispersion']['weather_risk_stats']}")
    print(f"conditions: {payload['dispersion']['condition_distribution']}")
    print(f"downwind consistency: {payload['dispersion']['downwind_consistency']}")
    print(f"wind averaging: vector {payload['wind_averaging']['vector_mean_direction']} "
          f"vs scalar {payload['wind_averaging']['scalar_mean_direction']} "
          f"(scalar error {payload['wind_averaging']['angular_error_of_scalar']} deg)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
