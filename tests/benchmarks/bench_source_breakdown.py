"""Benchmark: SourceAgent score breakdown on a roadside event + two contrasts.

The attribution summary in `bench_source_decision.py` answers "how often does
the main judgement name the traffic source". This script answers the follow-up
question a reviewer always asks: *how* is the judgement reached, and does the
same pipeline behave differently on cases that should NOT be traffic dominated?

Three windows are run end to end (air -> meteo -> traffic -> spatial -> source
-> decision), and for each one every intermediate score entering the five
attribution formulas is captured and re-computable:

  A. Mong Kok roadside, 2023-08-15 08:00-11:00  (the anchor event window)
  B. Mong Kok roadside, 2023-08-15 03:00-06:00  (low-anomaly contrast)
  C. Tai Po general station, 2023-08-15 08:00-11:00 (non-roadside contrast)

Usage: /usr/bin/python3 tests/benchmarks/bench_source_breakdown.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

ROADSIDE = {"case_id": "A_mongkok_roadside_peak", "station_name": "Mong Kok Roadside",
            "station_id": "mong_kok", "lat": 22.3227, "lon": 114.1683,
            "start_time": "2023-08-15 08:00:00", "end_time": "2023-08-15 11:00:00",
            "kind": "路边站 / 早高峰"}
LOW = {"case_id": "B_mongkok_roadside_night", "station_name": "Mong Kok Roadside",
       "station_id": "mong_kok", "lat": 22.3227, "lon": 114.1683,
       "start_time": "2023-08-15 03:00:00", "end_time": "2023-08-15 06:00:00",
       "kind": "路边站 / 凌晨低异常"}
GENERAL = {"case_id": "C_taipo_general_peak", "station_name": "TAI PO",
           "station_id": "tai_po", "lat": 22.4501, "lon": 114.1641,
           "start_time": "2023-08-15 08:00:00", "end_time": "2023-08-15 11:00:00",
           "kind": "一般站 / 早高峰"}

WINDOW_CASES = [ROADSIDE, LOW, GENERAL]


def run_case(case: dict) -> dict:
    from agents.air_quality_agent import AirQualityAgent
    from agents import meteorology_agent as met
    from agents import source_agent as source_module
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.traffic_agent import TrafficAgent
    from agents.decison_agent import run as decision_run

    task = {"task_id": f"BRK_{case['case_id']}",
            "station_name": case["station_name"], "station_id": case["station_id"],
            "lat": case["lat"], "lon": case["lon"],
            "start_time": case["start_time"], "end_time": case["end_time"]}

    air_agent = AirQualityAgent()
    air_out = air_agent.run(dict(task))
    meteo_out = met.run(dict(task))
    traffic_out = TrafficAgent().run(dict(task))
    spatial_agent = SpatialAgent()
    spatial_out = spatial_agent.run(dict(task), meteo_out)

    source_agent = SourceAgent()
    source_out = source_agent.run(task, air_result=air_out, meteo_result=meteo_out,
                                  traffic_result=traffic_out, spatial_result=spatial_out)
    decision_out = decision_run(source_out, spatial_out, traffic_out, meteo_out, dict(task), air_out)

    # 抓取进入五类评分公式的每一个分量（与 agents/source_agent.py 的 _extract_* 一致）
    air_scores, _ = source_agent._extract_air_scores(air_out)
    traffic_scores, _ = source_agent._extract_traffic_scores(traffic_out)
    meteo_scores, _ = source_agent._extract_meteorology_scores(meteo_out)
    spatial_scores, _ = source_agent._extract_spatial_scores(spatial_out)

    components = {
        "no2_score": air_scores.get("no2_score"),
        "anomaly_score": air_scores.get("anomaly_score"),
        "traffic_pressure_score": traffic_scores.get("traffic_score"),
        "upwind_score": spatial_scores.get("upwind_score"),
        "upwind_roads_count": spatial_scores.get("upwind_roads_count"),
        "static_wind_score": meteo_scores.get("static_wind_score"),
        "insufficient_rain_removal_score": meteo_scores.get("insufficient_rain_removal_score"),
        "roadside_boost": 0.85 if case["station_name"] in source_module.ROADSIDE_STATIONS else 0.4,
    }

    # 用抓到的分量重算一遍（校验公式可复现）
    recomputed_traffic = min(1.0, max(0.0,
        0.35 * components["no2_score"] + 0.25 * components["traffic_pressure_score"]
        + 0.20 * components["upwind_score"] + 0.10 * components["static_wind_score"]
        + 0.10 * components["roadside_boost"]))

    sr = source_out.get("result") or {}
    attr = sr.get("source_attribution") or {}
    ranked = sorted(((k, v) for k, v in attr.items() if isinstance(v, (int, float))),
                    key=lambda kv: kv[1], reverse=True)
    dec = decision_out.get("result") or {}

    return {
        "case": case["case_id"], "kind": case["kind"], "window": case["start_time"],
        "station": case["station_name"],
        "agent_status": {"air": air_out.get("status"), "meteo": meteo_out.get("status"),
                         "traffic": traffic_out.get("status"), "spatial": spatial_out.get("status"),
                         "source": source_out.get("status")},
        "components": components,
        "source_attribution": {k: round(v, 4) for k, v in attr.items() if isinstance(v, (int, float))},
        "ranking": [(k, round(v, 3)) for k, v in ranked],
        "traffic_source_rank": next((i + 1 for i, (k, _) in enumerate(ranked) if k == "traffic_source"), None),
        "recomputed_traffic_source": round(recomputed_traffic, 4),
        "formula_check_ok": abs(recomputed_traffic - attr.get("traffic_source", -1)) < 1e-3,
        "main_judgement": sr.get("main_judgement"),
        "decision_level": dec.get("decision_level"),
        "evidence": (sr.get("evidence") or [])[:6],
        "mean_wind_speed": meteo_scores.get("mean_wind_speed"),
        "wind_direction": meteo_scores.get("wind_direction"),
    }


def main() -> int:
    rows = []
    for case in WINDOW_CASES:
        try:
            row = run_case(case)
        except Exception as exc:  # noqa: BLE001
            print(f"  {case['case_id']:<28} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        rows.append(row)
        print(f"  {row['case']:<28} {row['kind']:<18} 异常={row['components']['anomaly_score']} "
              f"交通源={row['source_attribution'].get('traffic_source')} "
              f"（排名 {row['traffic_source_rank']}）判定={row['main_judgement']}", flush=True)

    payload = {
        "design": {"cases": [c["case_id"] for c in WINDOW_CASES],
                   "note": "同一链路在三类窗口上的评分分解与综合判定；分量与 source_agent 的 _extract_* 一致，"
                           "traffic_source 由分量按 0.35/0.25/0.20/0.10/0.10 权重重算校验"},
        "rows": rows,
    }
    path = save_result("source_breakdown", payload)
    print(f"\nsaved -> {path}")
    for row in rows:
        print(f"\n[{row['case']}] {row['kind']}  {row['window']}")
        print(f"  分量: {row['components']}")
        print(f"  五类评分: {json.dumps(row['source_attribution'], ensure_ascii=False)}")
        print(f"  排名: {row['ranking']}  交通源第 {row['traffic_source_rank']} 位")
        print(f"  综合判定: {row['main_judgement']} | 决策等级: {row['decision_level']}")
        print(f"  公式复算一致: {row['formula_check_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
