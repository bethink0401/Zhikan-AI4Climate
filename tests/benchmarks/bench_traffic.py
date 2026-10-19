"""Benchmark: TrafficAgent (rush-hour profile + weekday/weekend gap).

Measures, on the Mong Kok roadside station (ATC 3006):
  - traffic pressure across time slots (morning peak / midday / evening /
    weekend morning) — the expectation is a peak shape, i.e. the agent
    recovers the real rush-hour pattern from raw ATC counts
  - heavy-vehicle share stability
  - per-run latency

Usage: /usr/bin/python3 tests/benchmarks/bench_traffic.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROADSIDE_STATIONS, Timer, describe, save_result

from agents.traffic_agent import TrafficAgent

WEEKDAYS = ["2023-08-07", "2023-08-08", "2023-08-09", "2023-08-10", "2023-08-11"]
SLOTS = {
    "morning_peak": ("08:00:00", "11:00:00"),
    "midday_off": ("14:00:00", "17:00:00"),
    "evening": ("21:00:00", "23:00:00"),
}
#: ATC 源数据只提供 Mon-Fri 日型（day_type='Mon-Fri' 是唯一取值），
#: 因此本基准只覆盖工作日时段曲线，不做周末对照。


def main() -> int:
    mk = ROADSIDE_STATIONS[0]
    agent = TrafficAgent()
    timer = Timer()
    payload: dict = {"agent": "TrafficAgent"}

    slot_scores: dict[str, list[float]] = {k: [] for k in SLOTS}
    heavy_shares: list[float] = []
    runs = []
    for day in WEEKDAYS:
        for slot, (h0, h1) in SLOTS.items():
            task = {"task_id": f"BENCH_TRAF_{day}_{slot}",
                    "station_name": mk["station_name"],
                    "start_time": f"{day} {h0}", "end_time": f"{day} {h1}"}
            out = timer.wrap(agent.run, task)
            r = out.get("result") or {}
            score = r.get("traffic_pressure_score")
            slot_scores[slot].append(score if score is not None else 0.0)
            props = r.get("proportions_pct") or {}
            heavy = sum(v for k, v in props.items()
                        if any(t in str(k).lower() for t in ("bus", "goods", "truck")))
            heavy_shares.append(heavy)
            runs.append({"day": day, "slot": slot, "status": out.get("status"),
                         "score": score, "heavy_pct": round(heavy, 1)})
    weekend_scores = []  # 数据集无周末日型，保留空列表以兼容输出结构

    weekday_peak = describe(slot_scores["morning_peak"])
    payload["runs"] = runs
    payload["slot_means"] = {k: describe(v) for k, v in slot_scores.items()}
    payload["peak_vs_midday_gap_pct"] = round(
        (weekday_peak["mean"] - describe(slot_scores["midday_off"])["mean"])
        / max(describe(slot_scores["midday_off"])["mean"], 1e-9) * 100.0, 1)
    payload["peak_vs_evening_gap_pct"] = round(
        (weekday_peak["mean"] - describe(slot_scores["evening"])["mean"])
        / max(describe(slot_scores["evening"])["mean"], 1e-9) * 100.0, 1)
    payload["heavy_vehicle_share_pct"] = describe(heavy_shares)
    payload["latency_ms"] = timer.summary

    path = save_result("traffic", payload)
    print(f"saved -> {path}")
    for k, v in payload["slot_means"].items():
        print(f"{k}: mean {v.get('mean')}")
    print(f"peak vs midday: +{payload['peak_vs_midday_gap_pct']}%")
    print(f"peak vs evening: +{payload['peak_vs_evening_gap_pct']}%")
    print(f"heavy share: {payload['heavy_vehicle_share_pct']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
