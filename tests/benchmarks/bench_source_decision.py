"""Benchmark: SourceAgent + DecisionAgent (attribution + level mapping).

Part 1 — attribution on real high-anomaly windows: pick the top-5 NOx
hours of Mong Kok roadside in 2023 from the EPD database, run the
air -> meteo -> traffic -> spatial -> source chain on each, and check
how often the traffic source ranks first (or the main judgement names
traffic).

Part 2 — decision level mapping: get_decision_by_score on threshold
boundary values must map to the documented levels.

Usage: /usr/bin/python3 tests/benchmarks/bench_source_decision.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROADSIDE_STATIONS, Timer, save_result

from agents.air_quality_agent import AirQualityAgent
from agents import meteorology_agent as met
from agents.spatial_agent import SpatialAgent
from agents.source_agent import SourceAgent
from agents.traffic_agent import TrafficAgent
from agents.decison_agent import run as decision_run
from tools.desicion_rules import get_decision_by_score

MK = ROADSIDE_STATIONS[0]


def top_nox_windows(n: int = 5, year: str = "2023") -> list[tuple[str, str]]:
    conn = sqlite3.connect(Path(__file__).resolve().parents[2] / "data" / "air_quality.db")
    rows = conn.execute(
        "SELECT substr(time, 1, 13), nox FROM air_quality_hourly "
        "WHERE station_name = 'MONG KOK' AND time LIKE ? AND nox IS NOT NULL "
        "ORDER BY nox DESC LIMIT 40",
        (f"{year}%",),
    ).fetchall()
    conn.close()
    picked, seen_days = [], set()
    for hour_key, _nox in rows:
        day = hour_key.replace(" ", "T")[:10]
        if day in seen_days:
            continue
        seen_days.add(day)
        hour = int(hour_key[-2:])
        start = f"{day} {hour:02d}:00:00"
        end_h = (hour + 3) % 24
        end_day = day if end_h > hour else day  # 简化：窗口取同日内
        if end_h <= hour:
            end_day = day
        end = f"{end_day} {end_h:02d}:00:00"
        picked.append((start, end))
        if len(picked) >= n:
            break
    return picked


def main() -> int:
    timer = Timer()
    payload: dict = {"agent": "SourceAgent+DecisionAgent"}

    # -- part 1: attribution on top-anomaly windows ------------------------
    air_agent, traffic_agent = AirQualityAgent(), TrafficAgent()
    spatial_agent, source_agent = SpatialAgent(), SourceAgent()
    windows = top_nox_windows()
    attributions = []
    for start, end in windows:
        task = {"task_id": f"BENCH_SRC_{start[:10]}_{start[11:13]}",
                "station_name": MK["station_name"], "station_id": MK["station_id"],
                "start_time": start, "end_time": end,
                "lat": MK["lat"], "lon": MK["lon"]}
        air_out = timer.wrap(air_agent.run, dict(task))
        meteo_out = timer.wrap(met.run, dict(task))
        traffic_out = timer.wrap(traffic_agent.run, dict(task))
        spatial_out = timer.wrap(spatial_agent.run, task, meteo_out)
        source_out = timer.wrap(source_agent.run, task,
                                air_result=air_out, meteo_result=meteo_out,
                                traffic_result=traffic_out, spatial_result=spatial_out)
        decision_out = timer.wrap(decision_run, source_out, spatial_out,
                                  traffic_out, meteo_out, dict(task), air_out)
        sr = source_out.get("result") or {}
        attr = sr.get("source_attribution") or {}
        ranked = sorted(
            ((k, v) for k, v in attr.items() if isinstance(v, (int, float))),
            key=lambda kv: kv[1], reverse=True)
        attributions.append({
            "window": start,
            "air_anomaly": (air_out.get("result") or {}).get("anomaly_score"),
            "ranking": [(k, round(v, 3)) for k, v in ranked],
            "traffic_rank": [i for i, (k, _) in enumerate(ranked) if k == "traffic_source"],
            "main_judgement": sr.get("main_judgement"),
            "decision_level": (decision_out.get("result") or {}).get("decision_level"),
        })
    traffic_first = sum(1 for a in attributions
                        if (a["traffic_rank"] and a["traffic_rank"][0] == 0)
                        or (a["main_judgement"] or "").lower().startswith("traffic"))
    payload["attribution"] = {
        "windows": attributions,
        "traffic_named_first": f"{traffic_first}/{len(attributions)}",
        "note": "top-5 NOx hours (deduped per day) of Mong Kok roadside 2023",
    }

    # -- part 2: decision level mapping ------------------------------------
    # 实际阈值（tools/desicion_rules.py）：>=0.8 very_high / >=0.6 high / >=0.3 medium
    cases = [(0.2, "low"), (0.4, "medium"), (0.7, "high"), (0.85, "very_high")]
    mapping = []
    for score, expected in cases:
        decision = get_decision_by_score(score)
        level = (decision or {}).get("level") if isinstance(decision, dict) else decision
        mapping.append({"score": score, "expected": expected, "actual": level,
                        "ok": level == expected})
    payload["decision_mapping"] = {
        "cases": mapping,
        "all_ok": all(m["ok"] for m in mapping),
    }
    payload["latency_ms"] = timer.summary

    path = save_result("source_decision", payload)
    print(f"saved -> {path}")
    print(f"traffic named first: {payload['attribution']['traffic_named_first']}")
    for a in attributions:
        print(f"  {a['window']}  anomaly={a['air_anomaly']}  judgement={a['main_judgement']}  "
              f"level={a['decision_level']}")
    print(f"decision mapping ok: {payload['decision_mapping']['all_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
