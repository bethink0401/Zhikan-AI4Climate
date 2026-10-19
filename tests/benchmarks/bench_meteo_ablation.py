"""Benchmark: meteorological ablation — what breaks without wind data.

Two independent ablations:

A. Spatial propagation ablation (live, pure local compute):
   for the 3 roadside stations, generate the downwind analysis with
   (i) the true wind direction, (ii) wind direction rotated +90 degrees,
   (iii) no wind at all. Report whether the propagation sector exists,
   how many sensitive POIs fall inside it, and the warnings raised.

B. Travel decision ablation (from the stored 20-run case output):
   recompute each run's ranking with the pollution term removed from the
   utility and with the weather-burden term removed, and report how often
   the recommendation flips. This isolates how much of the decision
   actually depends on the meteorological and exposure terms.

Usage: /usr/bin/python3 tests/benchmarks/bench_meteo_ablation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROADSIDE_STATIONS, save_result

BASE = Path(__file__).resolve().parents[2]


def spatial_ablation() -> dict:
    from agents.spatial_agent import SpatialAgent

    agent = SpatialAgent()
    rows = []
    for station in ROADSIDE_STATIONS:
        task = {"task_id": f"ABLATE_{station['station_id']}",
                "station_name": station["station_name"],
                "station_id": station["station_id"],
                "lat": station["lat"], "lon": station["lon"]}
        variants = {
            "with_wind": {"dominant_wind_direction": 250.0,
                          "dominant_wind_direction_label": "WSW",
                          "mean_wind_speed": 2.0},
            "rotated_90": {"dominant_wind_direction": 340.0,
                           "dominant_wind_direction_label": "NNW",
                           "mean_wind_speed": 2.0},
            "no_wind": {"dominant_wind_direction": None,
                        "dominant_wind_speed": None},
        }
        for name, meteo in variants.items():
            out = agent.run(task, {"result": meteo})
            r = out.get("result") or {}
            geo = r.get("affected_area_geojson") or {}
            geometry_type = (geo.get("geometry") or {}).get("type") if geo else None
            rows.append({
                "station": station["station_name"],
                "variant": name,
                "status": out.get("status"),
                "upwind_roads": len(r.get("upwind_roads") or []),
                "sensitive_pois": len(r.get("sensitive_pois") or []),
                "affected_area_geometry": geometry_type,
                "warnings": len(out.get("warnings") or []),
            })
    return {"runs": rows}


def travel_ablation() -> dict:
    with open(BASE / "examples" / "travel_case_output.json") as f:
        doc = json.load(f)

    flips_no_pollution = flips_no_weather = runs = 0
    detail = []
    for scenario in doc.get("scenarios", []):
        for case in scenario.get("cases", []):
            for profile, run in case.get("runs", {}).items():
                options = (run.get("result") or {}).get("route_options") or []
                rec_id = ((run.get("result") or {}).get("recommendation") or {}).get("route_id")
                usable = [o for o in options if o.get("utility_detail")]
                if len(usable) < 2 or not rec_id:
                    continue
                runs += 1

                def argmin(key):
                    return min(usable, key=lambda o: o["utility_detail"].get(key, 0.0))["route_id"]

                def baseline():
                    return min(usable, key=lambda o: o.get("utility_minutes", 0.0))["route_id"]

                def no_term(term):
                    return min(usable, key=lambda o: (o.get("utility_minutes", 0.0)
                                                      - o["utility_detail"].get(term, 0.0)))["route_id"]

                base = baseline()
                p_flip = no_term("pollution_added_minutes") != base
                w_flip = no_term("weather_added_minutes") != base
                flips_no_pollution += int(p_flip)
                flips_no_weather += int(w_flip)
                detail.append({"scenario": scenario.get("scenario_id"),
                               "case": case.get("case_id"), "profile": profile,
                               "removing_pollution_flips": p_flip,
                               "removing_weather_flips": w_flip})

    return {"runs": runs,
            "pollution_term_removed_flips": flips_no_pollution,
            "weather_term_removed_flips": flips_no_weather,
            "detail": detail}


def main() -> int:
    payload = {
        "spatial_spread_ablation": spatial_ablation(),
        "travel_decision_ablation": travel_ablation(),
        "notes": [
            "spatial: 无风向时系统不生成定向传播扇区（退化为环形影响区并给出 warning）",
            "travel: 移去污染项或天气项后重排，统计推荐是否翻转；翻转即说明该项参与了决策",
        ],
    }
    path = save_result("meteo_ablation", payload)
    print(f"saved -> {path}")

    print("== A. spatial spread ablation ==")
    for row in payload["spatial_spread_ablation"]["runs"]:
        print(f"  {row['station'][:18]:18s} {row['variant']:10s} "
              f"roads={row['upwind_roads']:3d} pois={row['sensitive_pois']:4d} "
              f"area={row['affected_area_geometry']} warnings={row['warnings']}")

    t = payload["travel_decision_ablation"]
    print("== B. travel decision ablation ==")
    print(f"  runs={t['runs']}  removing pollution term flips recommendation: "
          f"{t['pollution_term_removed_flips']}/{t['runs']}")
    print(f"  removing weather term flips recommendation: "
          f"{t['weather_term_removed_flips']}/{t['runs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
