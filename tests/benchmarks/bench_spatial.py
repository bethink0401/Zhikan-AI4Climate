"""Benchmark: SpatialAgent (upwind road matching + sensitive POI coverage).

For each of the 3 roadside stations x 8 compass wind directions (2 m/s):
  - upwind candidate roads found (hit rate)
  - sensitive POIs inside the 3 km downwind sector
  - affected area produced
  - per-run latency

Usage: /usr/bin/python3 tests/benchmarks/bench_spatial.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROADSIDE_STATIONS, Timer, describe, save_result

from agents.spatial_agent import SpatialAgent

LABELS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def main() -> int:
    agent = SpatialAgent()
    timer = Timer()
    runs = []
    for st in ROADSIDE_STATIONS:
        task = {"task_id": f"BENCH_SPAT_{st['station_id']}",
                "station_name": st["station_name"],
                "station_id": st["station_id"],
                "lat": st["lat"], "lon": st["lon"]}
        for i, label in enumerate(LABELS):
            direction = i * 45.0
            meteo = {"result": {
                "dominant_wind_direction": direction,
                "dominant_wind_direction_label": label,
                "mean_wind_speed": 2.0,
            }}
            out = timer.wrap(agent.run, task, meteo)
            r = out.get("result") or {}
            runs.append({
                "station": st["station_name"],
                "wind_from_deg": direction,
                "status": out.get("status"),
                "upwind_roads": len(r.get("upwind_roads") or []),
                "sensitive_pois": len(r.get("sensitive_pois") or []),
                "has_affected_area": bool(r.get("affected_area_geojson")),
            })
    total = len(runs)
    payload = {
        "agent": "SpatialAgent",
        "runs": runs,
        "upwind_hit_rate": f"{sum(1 for r in runs if r['upwind_roads'] > 0)}/{total}",
        "upwind_roads_stats": describe([float(r["upwind_roads"]) for r in runs]),
        "poi_stats": describe([float(r["sensitive_pois"]) for r in runs]),
        "affected_area_rate": f"{sum(1 for r in runs if r['has_affected_area'])}/{total}",
        "latency_ms": timer.summary,
    }
    path = save_result("spatial", payload)
    print(f"saved -> {path}")
    print(f"upwind hit rate: {payload['upwind_hit_rate']}  roads: {payload['upwind_roads_stats']}")
    print(f"POIs in sector:  {payload['poi_stats']}")
    print(f"affected area:   {payload['affected_area_rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
