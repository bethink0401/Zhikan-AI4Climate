"""Benchmark: end-to-end pipeline stage timing (CoordinatorMin chain).

Replicates the CoordinatorMin orchestration on the validated Mong Kok
event task with per-stage wall-clock timers, 3 iterations in one process
(meteorology hits the network on the first pass only).

Usage: /usr/bin/python3 tests/benchmarks/bench_pipeline.py
"""
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROADSIDE_STATIONS, EVENT_WINDOW, save_result

from agents.air_quality_agent import AirQualityAgent
from agents import meteorology_agent as met
from agents.spatial_agent import SpatialAgent
from agents.source_agent import SourceAgent
from agents.traffic_agent import TrafficAgent
from agents.decison_agent import run as decision_run
from agents.report_agent import ReportAgent

STAGES = ["air", "meteo", "traffic", "spatial", "source", "decision", "report"]


def run_chain(agents, task):
    timings = {}
    t0 = time.perf_counter()
    air = agents["air"].run(dict(task));            timings["air"] = time.perf_counter() - t0
    t = time.perf_counter()
    meteo = agents["meteo"](dict(task));            timings["meteo"] = time.perf_counter() - t
    t = time.perf_counter()
    traffic = agents["traffic"].run(dict(task));    timings["traffic"] = time.perf_counter() - t
    t = time.perf_counter()
    spatial = agents["spatial"].run(task, meteo);   timings["spatial"] = time.perf_counter() - t
    t = time.perf_counter()
    source = agents["source"].run(task, air_result=air, meteo_result=meteo,
                                  traffic_result=traffic, spatial_result=spatial)
    timings["source"] = time.perf_counter() - t
    t = time.perf_counter()
    decision = decision_run(source, spatial, traffic, meteo, dict(task), air)
    timings["decision"] = time.perf_counter() - t
    t = time.perf_counter()
    report = agents["report"].run({"task": task, "air_quality": air,
                                   "meteorology": meteo, "traffic": traffic,
                                   "spatial": spatial, "source": source,
                                   "decision": decision})
    timings["report"] = time.perf_counter() - t
    statuses = {
        "air": air.get("status"), "meteo": meteo.get("status"),
        "traffic": traffic.get("status"), "spatial": spatial.get("status"),
        "source": source.get("status"), "decision": decision.get("status"),
        "report": report.get("status"),
    }
    judgement = (source.get("result") or {}).get("main_judgement")
    return timings, statuses, judgement


def main() -> int:
    mk = ROADSIDE_STATIONS[0]
    task = {"task_id": "BENCH_PIPELINE_MK",
            "station_name": mk["station_name"], "station_id": mk["station_id"],
            "start_time": EVENT_WINDOW[0], "end_time": EVENT_WINDOW[1],
            "lat": mk["lat"], "lon": mk["lon"]}
    agents = {
        "air": AirQualityAgent(), "meteo": met.run, "traffic": TrafficAgent(),
        "spatial": SpatialAgent(), "source": SourceAgent(), "report": ReportAgent(),
    }

    per_stage = defaultdict(lambda: defaultdict(list))
    runs = []
    for i in range(3):
        timings, statuses, judgement = run_chain(agents, task)
        for stage, seconds in timings.items():
            per_stage[stage]["ms"].append(round(seconds * 1000.0, 1))
        per_stage["total"]["ms"].append(round(sum(timings.values()) * 1000.0, 1))
        runs.append({"iteration": i + 1, "total_ms": round(sum(timings.values()) * 1000.0, 1),
                     "statuses": statuses, "main_judgement": judgement})

    payload = {
        "pipeline": "CoordinatorMin chain (air->meteo->traffic->spatial->source->decision->report)",
        "task": {"station": mk["station_name"], "window": list(EVENT_WINDOW)},
        "runs": runs,
        "stage_ms": {stage: dict(vals) for stage, vals in per_stage.items()},
        "note": "iteration 1 includes the Open-Meteo fetch; iterations 2-3 are warm",
    }
    path = save_result("pipeline", payload)
    print(f"saved -> {path}")
    for r in runs:
        print(f"run {r['iteration']}: {r['total_ms']}ms  judgement={r['main_judgement']}")
    print("stages (mean ms):",
          {s: round(sum(v["ms"]) / len(v["ms"]), 1) for s, v in per_stage.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
