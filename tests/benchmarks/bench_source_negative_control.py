"""Benchmark: negative controls for the source-attribution rule.

The positive benchmark (top-5 NOx windows at a roadside station) shows the rule
names the traffic source on traffic-dominated windows. That alone cannot tell
whether the rule can *avoid* naming traffic when traffic is weak — a rule with a
strong traffic prior would also score 5/5 there.

This script runs the same chain on three negative controls: general/background
stations, hours chosen for weak traffic signatures (high O3 with low NO2, and a
very-low-NO2 background day). The pass criterion is fixed in advance:

    PASS  = the main judgement does NOT name the traffic source
            (i.e. photochemical / regional / mixed-uncertain)
    FAIL  = the main judgement names the traffic source

Reported as-is; failures are the point of a negative control.

Usage: /usr/bin/python3 tests/benchmarks/bench_source_negative_control.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

STATIONS = {
    "tap_mun": {"station_name": "TAP MUN", "station_name_zh": "塔门",
                "station_id": "tap_mun", "lat": 22.471, "lon": 114.27},
    "tai_po": {"station_name": "TAI PO", "station_name_zh": "大埔",
               "station_id": "tai_po", "lat": 22.451, "lon": 114.168},
}

CASES = [
    {"case_id": "N1_photochemical_pm", "station": "tap_mun", "kind": "光化学特征时段（O₃ 高、NO₂ 低）",
     "start_time": "2023-07-26 16:00:00", "end_time": "2023-07-26 19:00:00", "expected": "不含交通源"},
    {"case_id": "N2_background_day", "station": "tap_mun", "kind": "极低 NO₂ 背景日",
     "start_time": "2023-10-02 09:00:00", "end_time": "2023-10-02 12:00:00", "expected": "不含交通源"},
    {"case_id": "N3_general_station", "station": "tai_po", "kind": "一般站低 NO₂ 时段",
     "start_time": "2023-07-17 08:00:00", "end_time": "2023-07-17 11:00:00", "expected": "不含交通源"},
]

TRAFFIC_WORDS = ("traffic",)


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents import meteorology_agent as met
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.traffic_agent import TrafficAgent
    from agents.decison_agent import run as decision_run

    air_agent, traffic_agent = AirQualityAgent(), TrafficAgent()
    spatial_agent, source_agent = SpatialAgent(), SourceAgent()
    rows = []

    import sqlite3
    conn = sqlite3.connect(BASE / "data" / "air_quality.db")

    for case in CASES:
        st = STATIONS[case["station"]]
        task = {"task_id": f"NEG_{case['case_id']}", "station_name": st["station_name"],
                "station_id": st["station_id"], "lat": st["lat"], "lon": st["lon"],
                "start_time": case["start_time"], "end_time": case["end_time"]}

        # 该窗口的真实污染物水平（用于说明"交通特征弱"）
        pol = conn.execute(
            "SELECT AVG(no2), AVG(nox), AVG(pm10), MAX(o3), AVG(co) FROM air_quality_hourly "
            "WHERE station_name = ? AND time >= ? AND time <= ?",
            (st["station_name"], case["start_time"], case["end_time"])).fetchone()

        air_out = air_agent.run(dict(task))
        meteo_out = met.run(dict(task))
        traffic_out = traffic_agent.run(dict(task))
        spatial_out = spatial_agent.run(dict(task), meteo_out)
        source_out = source_agent.run(task, air_result=air_out, meteo_result=meteo_out,
                                      traffic_result=traffic_out, spatial_result=spatial_out)
        decision_out = decision_run(source_out, spatial_out, traffic_out, meteo_out, dict(task), air_out)

        sr = source_out.get("result") or {}
        attr = {k: round(v, 4) for k, v in (sr.get("source_attribution") or {}).items()
                if isinstance(v, (int, float))}
        judgement = sr.get("main_judgement") or ""
        ranked = sorted(attr.items(), key=lambda kv: kv[1], reverse=True)
        names_traffic = any(w in judgement.lower() for w in TRAFFIC_WORDS)
        row = {
            "case": case["case_id"], "kind": case["kind"], "station": st["station_name_zh"],
            "window": case["start_time"], "expected": case["expected"],
            "pollutants": {"no2_avg": None if pol[0] is None else round(pol[0], 1),
                           "nox_avg": None if pol[1] is None else round(pol[1], 1),
                           "pm10_avg": None if pol[2] is None else round(pol[2], 1),
                           "o3_max": None if pol[3] is None else round(pol[3], 1),
                           "co_avg": None if pol[4] is None else round(pol[4], 2)},
            "anomaly_score": (air_out.get("result") or {}).get("anomaly_score"),
            "traffic_pressure": (traffic_out.get("result") or {}).get("traffic_pressure_score"),
            "upwind_road_count": len((spatial_out.get("result") or {}).get("upwind_roads") or []),
            "source_attribution": attr,
            "top_source": ranked[0][0] if ranked else None,
            "traffic_rank": next((i + 1 for i, (k, _) in enumerate(ranked) if k == "traffic_source"), None),
            "main_judgement": judgement,
            "decision_level": (decision_out.get("result") or {}).get("decision_level"),
            "names_traffic": names_traffic,
            "pass": not names_traffic,
        }
        rows.append(row)
        print(f"  {case['case_id']:<22} {st['station_name_zh']} {case['start_time'][:13]} | "
              f"NO₂ {row['pollutants']['no2_avg']} O₃max {row['pollutants']['o3_max']} | "
              f"异常 {row['anomaly_score']} | 交通源 {attr.get('traffic_source')}（第 {row['traffic_rank']}）| "
              f"判定 {judgement} | {'通过' if row['pass'] else '未通过'}", flush=True)

    conn.close()
    passed = sum(1 for r in rows if r["pass"])
    payload = {
        "design": {"criterion": "综合判定不含交通源 = 通过；含交通源 = 未通过",
                   "cases": [c["case_id"] for c in CASES],
                   "note": "负对照：一般站/背景站上交通特征弱（O₃ 高 NO₂ 低、极低 NO₂ 背景日）的窗口"},
        "passed": f"{passed}/{len(rows)}",
        "rows": rows,
    }
    path = save_result("source_negative_control", payload)
    print(f"\nsaved -> {path}")
    print(f"负对照通过率: {payload['passed']}（判据：综合判定不含交通源）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
