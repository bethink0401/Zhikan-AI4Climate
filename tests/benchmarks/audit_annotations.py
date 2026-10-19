"""Audit: annotation consistency check for the governance benchmark labels.

The benchmark's F1 measures agreement with criteria-based annotations, so the
annotations themselves must be internally consistent and reproducible. This
script:

  1. recomputes the fixed criteria from the raw DB (independent of the stored
     results) and compares them to the labels stored in
     governance_baselines.json (self-consistency);
  2. cross-checks each labelled window against **auxiliary** evidence that the
     criteria did not use, as a plausibility probe:
       - traffic windows     : NOx window-mean percentile (should be high),
                               diurnal signal present;
       - regional windows    : multi-station sync recomputed via the shipped
                               AirQualityAgent tooling (independent path);
       - accumulation windows: wind speed and NO2 level;
       - uncertain windows   : none of the criteria fires (by construction) —
                               report NO2 level distribution for context.

Usage: /usr/bin/python3 tests/benchmarks/audit_annotations.py
"""
from __future__ import annotations

import json
import statistics
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from bench_governance_baselines import (  # noqa: E402
    DB_PATH,
    YEAR,
    build_candidate_windows,
    load_annual_wind,
)


def main() -> int:
    wind = load_annual_wind()
    conn = sqlite3.connect(DB_PATH)
    candidates = build_candidate_windows(conn, wind)
    criteria = {(c["date"], c["start_hour"]): c for c in candidates}
    conn.close()

    stored = json.loads(
        (Path(__file__).resolve().parent / "results" / "governance_baselines.json")
        .read_text(encoding="utf-8"))["results"]

    # ── 1. 自洽性：存储标签 vs 独立重算 ─────────────────────────────────────
    mismatches = []
    for row in stored:
        window = row["window"]
        key = (window["date"], window["start_hour"])
        recomputed = criteria.get(key)
        if recomputed is None:
            mismatches.append({**window, "issue": "候选集里找不到该窗口"})
        elif recomputed["label"] != window["label"]:
            mismatches.append({**window, "issue":
                               f"存储 {window['label']} vs 重算 {recomputed['label']}"})
        elif recomputed["sync_count"] != window["sync_count"]:
            mismatches.append({**window, "issue":
                               f"sync_count {window['sync_count']} vs 重算 {recomputed['sync_count']}"})
    print(f"自洽性: {len(stored) - len(mismatches)}/{len(stored)} 一致")

    # ── 2. 辅助证据核查（判据未用到的独立信号） ─────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    aux = {"traffic_dominant": [], "local_accumulation": [],
           "regional_transport": [], "uncertain": []}
    nox_population = sorted(v for (v,) in conn.execute(
        "SELECT nox FROM air_quality_hourly WHERE station_name='MONG KOK' "
        "AND station_type='roadside' AND time LIKE ? AND nox IS NOT NULL", (f"{YEAR}-%",)))

    from tools.air_quality_tools import load_station_no2_p90, query_multi_station_sync

    for row in stored:
        window = row["window"]
        label = window["label"]
        times = [f"{window['date']} {h:02d}:00:00"
                 for h in range(window["start_hour"], window["start_hour"] + 3)]
        if label == "traffic_dominant":
            nox_pct = percentile_rank(nox_population, window["mean_nox"])
            aux[label].append({"date": window["date"],
                               "nox_percentile": round(nox_pct, 3),
                               "is_peak": window["start_hour"] in (7, 18)})
        elif label == "regional_transport":
            sync = query_multi_station_sync(
                conn, times[0], times[-1].replace(":00:00", ":59:59"),
                load_station_no2_p90(conn, YEAR))
            aux[label].append({"date": window["date"],
                               "independent_sync": sync["stations_above_p90"],
                               "wind": window["mean_wind"]})
        elif label == "local_accumulation":
            aux[label].append({"date": window["date"],
                               "wind": window["mean_wind"],
                               "no2": window["mean_no2"]})
        else:
            aux[label].append({"date": window["date"], "no2": window["mean_no2"]})
    conn.close()

    report = {"self_consistency": f"{len(stored) - len(mismatches)}/{len(stored)}",
              "mismatches": mismatches, "auxiliary_checks": {}}
    for label, items in aux.items():
        entry = {"n": len(items)}
        if label == "traffic_dominant":
            entry["nox_percentile_min"] = min(i["nox_percentile"] for i in items) if items else None
            entry["nox_percentile_mean"] = round(statistics.mean(
                i["nox_percentile"] for i in items), 3) if items else None
            entry["all_peak"] = all(i["is_peak"] for i in items)
        elif label == "regional_transport":
            entry["independent_sync_min"] = min(i["independent_sync"] for i in items) if items else None
            entry["independent_sync_mean"] = round(statistics.mean(
                i["independent_sync"] for i in items), 2) if items else None
            entry["all_wind_ge_3"] = all((i["wind"] or 0) >= 3.0 for i in items)
        elif label == "local_accumulation":
            entry["wind_max"] = max((i["wind"] or 9) for i in items) if items else None
        else:
            entry["no2_mean"] = round(statistics.mean(i["no2"] for i in items), 1) if items else None
        report["auxiliary_checks"][label] = entry
        print(f"  {label}: {entry}")

    path = save_result("annotation_audit", report)
    print(f"saved -> {path}")
    if mismatches:
        print("不一致明细:", mismatches[:5])
    return 0


def percentile_rank(population: list[float], value: float) -> float:
    if not population:
        return 0.0
    return sum(1 for v in population if v < value) / len(population)


if __name__ == "__main__":
    sys.exit(main())
