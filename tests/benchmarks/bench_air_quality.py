"""Benchmark: AirQualityAgent (anomaly detection + data completeness).

Three measurable separations, all on real EPD data:
  1. time-slot separation: morning-peak anomaly vs late-night anomaly at
     the Mong Kok roadside station (the agent should recover the
     diurnal pollution rhythm)
  2. station-type separation: roadside (Mong Kok) vs background station
     (Tap Mun) on the same windows — the roadside boost must widen the
     gap, not blur it
  3. the validated Mong Kok event window (2023-08-15 08:00-11:00)

Plus EPD hourly-table completeness for 2023 and per-run latency.

Usage: /usr/bin/python3 tests/benchmarks/bench_air_quality.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EVENT_WINDOW, ROADSIDE_STATIONS, Timer, describe, save_result

from agents.air_quality_agent import AirQualityAgent

MK = ROADSIDE_STATIONS[0]
BACKGROUND = {"station_name": "TAP MUN", "station_id": "tap_mun",
              "lat": 22.4716, "lon": 114.2591}
WEEKDAYS = ["2023-08-07", "2023-08-08", "2023-08-09", "2023-08-10", "2023-08-11"]


def completeness(station_label: str, year: int = 2023) -> dict:
    """按站名前缀解析库内实际站名（agent 层做别名归一，库里是大写缩写）。"""
    conn = sqlite3.connect(Path(__file__).resolve().parents[2] / "data" / "air_quality.db")
    db_name = conn.execute(
        "SELECT DISTINCT station_name FROM air_quality_hourly "
        "WHERE station_name LIKE ? LIMIT 1",
        (station_label.split()[0] + "%",)).fetchone()
    db_name = db_name[0] if db_name else station_label
    hours = conn.execute(
        "SELECT COUNT(DISTINCT time) FROM air_quality_hourly "
        "WHERE station_name = ? AND time LIKE ?",
        (db_name, f"{year}%")).fetchone()[0]
    rows = conn.execute(
        "SELECT COUNT(*) FROM air_quality_hourly WHERE station_name = ? AND time LIKE ?",
        (db_name, f"{year}%")).fetchone()[0]
    conn.close()
    return {"year": year, "station": db_name,
            "distinct_hours": hours, "expected_hours": 8760,
            "duplicate_rows": rows - hours,
            "completeness_pct": round(hours / 8760 * 100.0, 2)}


def run_window(agent: dict, station: dict, day: str, h0: str, h1: str) -> dict:
    task = {"task_id": f"BENCH_AQ_{station['station_id']}_{day}_{h0[:2]}",
            "station_name": station["station_name"],
            "station_id": station["station_id"],
            "start_time": f"{day} {h0}", "end_time": f"{day} {h1}",
            "lat": station["lat"], "lon": station["lon"]}
    out = agent["air"].run(task)
    r = out.get("result") or {}
    return {"status": out.get("status"),
            "anomaly_score": r.get("anomaly_score"),
            "traffic_signal": r.get("traffic_pollutant_signal"),
            "primary": r.get("primary_pollutants")}


def main() -> int:
    timer = Timer()
    mk_agent = {"air": AirQualityAgent()}
    bg_agent = {"air": AirQualityAgent()}
    payload: dict = {"agent": "AirQualityAgent"}

    payload["completeness_mk_2023"] = completeness(MK["station_name"])

    # 1. time-slot separation at the roadside station ----------------------
    peak, night = [], []
    peak_signal = night_signal = 0
    for day in WEEKDAYS:
        p = timer.wrap(run_window, mk_agent, MK, day, "08:00:00", "11:00:00")
        n = timer.wrap(run_window, mk_agent, MK, day, "02:00:00", "05:00:00")
        peak.append(p); night.append(n)
        peak_signal += "detected" in str(p["traffic_signal"]).lower()
        night_signal += "detected" in str(n["traffic_signal"]).lower()
    peak_scores = [x["anomaly_score"] or 0.0 for x in peak]
    night_scores = [x["anomaly_score"] or 0.0 for x in night]
    payload["slot_separation"] = {
        "morning_peak": describe(peak_scores),
        "late_night": describe(night_scores),
        "gap_pct": round((describe(peak_scores)["mean"] - describe(night_scores)["mean"])
                         / max(describe(night_scores)["mean"], 1e-9) * 100.0, 1),
        "traffic_signal_detected": f"peak {peak_signal}/5 vs night {night_signal}/5",
        "windows": {"peak": peak, "night": night},
    }

    # 2. roadside vs background on the same peak windows --------------------
    bg_scores = []
    bg_runs = []
    for day in WEEKDAYS:
        r = timer.wrap(run_window, bg_agent, BACKGROUND, day, "08:00:00", "11:00:00")
        bg_scores.append(r["anomaly_score"] or 0.0)
        bg_runs.append({"day": day, **r})
    payload["station_type_separation"] = {
        "roadside": describe(peak_scores),
        "background": describe(bg_scores),
        "background_station": BACKGROUND["station_name"],
        "background_runs": bg_runs,
        "ratio": round(describe(peak_scores)["mean"]
                       / max(describe(bg_scores)["mean"], 1e-9), 2),
    }

    # 3. validated event window --------------------------------------------
    event = timer.wrap(run_window, mk_agent, MK, EVENT_WINDOW[0][:10], "08:00:00", "11:00:00")
    payload["event"] = {"window": list(EVENT_WINDOW), **event}

    payload["latency_ms"] = timer.summary
    path = save_result("air_quality", payload)
    print(f"saved -> {path}")
    s = payload["slot_separation"]
    print(f"slot separation: peak {s['morning_peak']['mean']} vs night {s['late_night']['mean']} "
          f"(+{s['gap_pct']}%), signal: {s['traffic_signal_detected']}")
    t = payload["station_type_separation"]
    print(f"station separation: roadside {t['roadside']['mean']} vs background "
          f"{t['background']['mean']} (x{t['ratio']})")
    print(f"event: anomaly {payload['event']['anomaly_score']}, "
          f"signal {payload['event']['traffic_signal']}")
    print(f"completeness: {payload['completeness_mk_2023']['completeness_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
