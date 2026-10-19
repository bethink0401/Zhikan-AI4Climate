"""Live timing measurement for the competition deck.

Runs TravelAgent.run() on the validated TST -> Prince Edward commuter
request three times in one process and reports wall-clock time per run,
plus the database scale numbers for the data section of the deck.

Usage: /usr/bin/python3 tests/benchmark_timing.py
"""
import json
import os
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def db_scale():
    out = {}
    for name, path, table in (
        ("air_quality", os.path.join(BASE, "data", "air_quality.db"), "air_quality_hourly"),
        ("traffic", os.path.join(BASE, "data", "traffic.db"), "traffic_vehicle_mix"),
    ):
        try:
            conn = sqlite3.connect(path)
            out[name] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.close()
        except Exception as exc:  # noqa: BLE001 - report and continue
            out[name] = f"error: {exc}"
    return out


def main():
    print("== data scale ==")
    for key, value in db_scale().items():
        print(f"{key}: {value}")

    print()
    print("== TravelAgent.run() timing ==")
    from agents.travel_agent import TravelAgent

    with open(os.path.join(BASE, "examples", "travel_case_request.json")) as f:
        task = json.load(f)

    agent = TravelAgent()
    for i in range(3):
        t0 = time.perf_counter()
        result = agent.run(dict(task))
        dt = time.perf_counter() - t0
        status = result.get("status")
        n_routes = len((result.get("result") or {}).get("route_options") or [])
        print(f"run {i + 1}: {dt:.2f}s status={status} routes={n_routes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
