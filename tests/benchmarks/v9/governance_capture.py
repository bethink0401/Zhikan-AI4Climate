"""V9.6：真实数据（2024 留出年）逐窗组件得分捕获。

目的：把 254 个留出窗口的全部**原始可观测量**一次性录下来，之后任何判定规则
变体都能离线扫描（governance_sweep.py），不必每次重跑 13 分钟的完整链路。

同时录两种基线口径：
- ``full``   : 全年分布基线（历史回测口径，与 V9 留出可对齐）；
- ``pit``    : 滚动 12 个月截断基线（实时可用口径，与 V9 PIT 可对齐）。

字段设计原则：只记录**观测量与组件原值**，不记录任何派生判定——判定由
governance_sweep.py 用显式权重重算，保证规则改动可审计、可复现。
"""

from __future__ import annotations

import csv
import json
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
for path in (str(BASE), str(HERE.parent), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import bench_governance_v6 as gv6  # noqa: E402
import meteo_cache  # noqa: E402
from contracts import ContractViolation, require_no_all_null  # noqa: E402

YEAR = 2024
# 与 governance_holdout.py 完全一致，保证 254 窗可与 V9.4/V9.5 逐例对齐
SAMPLE_PLAN = {"traffic_dominant": 30, "local_accumulation": 18,
               "regional_transport": 24, "uncertain": 24, "weekend_negative": 12}
SEED = 2024


def _upwind_from_spatial(spatial_result: dict) -> float | None:
    """与 SourceAgent 同口径：上风向道路的最大对齐度。"""
    result = spatial_result.get("result") or {}
    roads = result.get("upwind_roads") or []
    if not roads:
        return None
    return max(float(road.get("upwind_alignment") or 0.0) for road in roads)


def _components(air: dict, meteo: dict, traffic: dict, spatial: dict) -> dict:
    air_res = air.get("result") or {}
    meteo_res = meteo.get("result") or {}
    traffic_res = traffic.get("result") or {}
    sync = air_res.get("multi_station_sync") or {}
    contrast = air_res.get("spatial_contrast") or {}
    return {
        "anomaly": air_res.get("anomaly_score"),
        "nox_pct": air_res.get("nox_year_percentile"),
        "no2_signal": air_res.get("traffic_pollutant_signal_score"),
        "sync_checked": sync.get("stations_checked"),
        "sync_above": sync.get("stations_above_p90"),
        "contrast_checked": contrast.get("stations_checked"),
        "contrast_mean_ratio": contrast.get("mean_ratio"),
        "contrast_cv": contrast.get("cv"),
        "contrast_station_ratio": contrast.get("station_ratio"),
        "static_wind": meteo_res.get("static_wind_score"),
        "wind_shift": meteo_res.get("wind_shift_score"),
        "mean_wind": meteo_res.get("mean_wind_speed"),
        "traffic_pressure": traffic_res.get("traffic_pressure_score"),
        "upwind_score": _upwind_from_spatial(spatial),
        "air_status": air.get("status"),
        "meteo_status": meteo.get("status"),
        "traffic_status": traffic.get("status"),
        "spatial_status": spatial.get("status"),
    }


def capture() -> list[dict]:
    # 气象走年度磁盘缓存：每站整年取一次（3 次请求替代 254 次），
    # 取数层注入、Agent 公式不变，自检见 meteo_cache.main()。
    meteo_cache.install(HERE.parents[0] / "results" / "meteo_cache")
    from agents import meteorology_agent as met
    from agents.air_quality_agent import AirQualityAgent
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.traffic_agent import TrafficAgent

    conn = sqlite3.connect(gv6.DB_PATH)
    air_agent, traffic_agent = AirQualityAgent(), TrafficAgent()
    spatial_agent, source_agent = SpatialAgent(), SourceAgent()

    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    rows: list[dict] = []
    for station in gv6.STATIONS:
        if wanted and station["name"] not in wanted and station["id"] not in wanted:
            continue
        print(f"=== {station['name']} / {YEAR} ===", flush=True)
        wind = gv6.load_annual_wind(YEAR, station["lat"], station["lon"])
        candidates = gv6.build_windows(conn, station["db_name"], YEAR, wind)

        rng = random.Random(SEED)
        picked = []
        for label, count in SAMPLE_PLAN.items():
            if label == "weekend_negative":
                pool = [c for c in candidates if not c["is_weekday"]]
            else:
                pool = [c for c in candidates
                        if c["label"] == label and c["is_weekday"]]
            rng.shuffle(pool)
            for item in pool[:count]:
                row = dict(item)
                row["sample_group"] = label
                picked.append(row)
        print(f"  抽样 {len(picked)} 窗", flush=True)

        no2_population = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? "
            "AND station_type='roadside' AND time LIKE ? AND no2 IS NOT NULL",
            (station["db_name"], f"{YEAR}-%")))
        nox_population = sorted(v for (v,) in conn.execute(
            "SELECT nox FROM air_quality_hourly WHERE station_name=? "
            "AND station_type='roadside' AND time LIKE ? AND nox IS NOT NULL",
            (station["db_name"], f"{YEAR}-%")))

        for index, window in enumerate(picked):
            task = {"task_id": f"CAP_{station['id']}_{window['date']}_{window['start_hour']}",
                    "station_name": station["name"], "station_id": station["id"],
                    "lat": station["lat"], "lon": station["lon"],
                    "start_time": window["start_time"], "end_time": window["end_time"]}
            meteo = met.run(dict(task))
            traffic = traffic_agent.run(dict(task))
            spatial = spatial_agent.run(dict(task), meteo)

            entry = {
                "station": station["name"], "station_db": station["db_name"],
                "date": window["date"], "start_hour": window["start_hour"],
                "label": window["label"], "sample_group": window["sample_group"],
                "is_weekday": window["is_weekday"],
                "mean_no2": window["mean_no2"], "mean_nox": window["mean_nox"],
            }
            for mode, pit in (("full", False), ("pit", True)):
                air = air_agent.run({**task, "point_in_time": pit})
                comp = _components(air, meteo, traffic, spatial)
                for key, value in comp.items():
                    entry[f"{mode}_{key}"] = value
                source = source_agent.run(
                    {"task_id": task["task_id"], "station_name": station["name"]},
                    air_result=air, meteo_result=meteo, traffic_result=traffic,
                    spatial_result=spatial)
                sres = source.get("result") or {}
                entry[f"{mode}_signature"] = json.dumps(
                    sres.get("source_attribution", {}).get("signature_scores") or {},
                    ensure_ascii=False)
                entry[f"{mode}_signature_available"] = json.dumps(
                    sres.get("source_attribution", {}).get("signature_available") or {},
                    ensure_ascii=False)
                entry[f"{mode}_judgement"] = sres.get("main_judgement")
            rows.append(entry)
            print(f"  [{index + 1}/{len(picked)}] {window['date']} "
                  f"{window['start_hour']:02d}时 标注={window['label']:<19} "
                  f"pit判={entry['pit_judgement']}", flush=True)

    conn.close()
    return rows


def main() -> int:
    rows = capture()

    # ── 先固化产物：契约失败不得丢结果（V9.6 教训：断言放在写盘前，
    #    三次重跑的结果全部丢失）。──
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    tag = "_".join(sorted(wanted)) if wanted else ""
    name = ("v96_real_components.csv" if not tag
            else f"v96_real_components_{tag}.csv")
    path = HERE / "results" / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nsaved -> {path}（{len(rows)} 窗）", flush=True)

    # ── 数据契约（V9.6）：静默失效一律报错，不静默继续 ──
    checked = ("nox_pct", "traffic_pressure", "upwind_score", "static_wind",
               "mean_wind", "sync_above")
    violations: list[str] = []
    for mode in ("full", "pit"):
        require_no_all_null(rows, tuple(f"{mode}_{f}" for f in checked),
                            context=f"real capture / {mode}")
    degraded = sorted({r["date"] for r in rows
                       for mode in ("full", "pit")
                       if str(r.get(f"{mode}_meteo_status")) != "success"})
    if degraded:
        violations.append(f"气象非 success 的窗口日期 {len(degraded)} 个：{degraded[:5]}")
    report = {
        "n_windows": len(rows),
        "feature_columns_checked": list(checked),
        "all_null_features": "none",
        "meteo_degraded_dates": degraded,
        "verdict": "PASS" if not violations else "FAIL",
        "violations": violations,
    }
    (HERE / "results" / f"v96_contract_report{('_' + tag) if tag else ''}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"数据契约：{report['verdict']}（{len(checked)} 个特征无整列全空；"
          f"气象降级窗 {len(degraded)} 个）", flush=True)
    if violations:
        raise ContractViolation("；".join(violations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
