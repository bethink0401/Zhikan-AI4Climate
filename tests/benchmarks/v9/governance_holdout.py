"""V9 Experiment 1: innovation-1 generalisation on a NEVER-USED holdout year.

Per the V9 protocol:
  - Year 2024 (verified absent from every V1-V8 training/threshold/error-analysis
    run), three roadside stations, ~100 event windows across all label classes;
  - A (single-source rule), B (multi-source rule, same multi-station sync
    evidence as C), B+ (strengthened rule baseline: B's inputs plus the V8
    structural mechanisms — NOx-year-percentile gate, regional-first priority,
    local-accumulation branch — to isolate extra-input value from mechanism
    value), C (full agent system, frozen V8 config, zero re-tuning);
  - Rule-label generalisation check ONLY: human blind rating is PENDING
    (template provided); no claim of true source-attribution accuracy;
  - Clustered (by date) paired bootstrap CI for C−B; confusion matrices;
    coverage-risk curve guarding against abstention-inflated F1.

Usage: /usr/bin/python3 tests/benchmarks/v9/governance_holdout.py [YEAR]

YEAR 默认 2024（V9 的开发/留出年）。传入 2021 时是**一次性盲测运行**：不改变任何
参数、抽样计划与阈值，只换年份；结果写到独立的 `v9_governance_blind2021.*`，
**不覆盖** 2024 的任何产物（配置审计的在线/离线比对依赖后者）。
"""
from __future__ import annotations

import csv
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from metrics import (clustered_macro_f1_diff, confusion, coverage_risk_curve,
                     macro_f1)  # noqa: E402
import bench_governance_v6 as gv6  # noqa: E402

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2024
#: 非默认年份 = 一次性盲测：产物加后缀，绝不覆盖开发年结果。
TAG = "" if YEAR == 2024 else f"_blind{YEAR}"
#: 放大样本计划（池不足时如实报告实际 n，不凑数）
SAMPLE_PLAN = {"traffic_dominant": 30, "local_accumulation": 18,
               "regional_transport": 24, "uncertain": 24, "weekend_negative": 12}


def method_bplus(window: dict, station: dict, nox_population: list[float],
                 no2_population: list[float], conn) -> str:
    """B+ 强化规则基线：B 的输入 + V8 结构机制（全部规则层，无智能体输入）。

    与 B 的差异 = C 在 V8 获益的三个结构机制：①交通判定加 NOx 年内分位
    门控；②区域传输优先于交通；③积聚独立分支。
    """
    from bench_governance_v6 import method_b_v6  # 复用 B 的评分

    # 第一步：沿用 B 的三组分
    nox_pct = statistics.mean([1.0 if v < window["mean_nox"] else 0.0
                               for v in nox_population]) if nox_population else 0.0
    no2_pct = statistics.mean([1.0 if v < window["mean_no2"] else 0.0
                               for v in no2_population]) if no2_population else 0.0
    wind_speed = window["mean_wind"]
    if wind_speed is None:
        low_wind = high_wind = 0.5
    else:
        low_wind = max(0.0, min(1.0, (2.5 - wind_speed) / 2.5))
        high_wind = max(0.0, min(1.0, wind_speed / 5.0))
    sync_norm = min(1.0, window["sync_count"] / 8.0)
    traffic_score = 0.8 * nox_pct + 0.2 * (1.0 if "ROADSIDE" in station["db_name"] else 0.4)
    accumulation_score = 0.6 * low_wind + 0.4 * no2_pct
    regional_score = 0.7 * sync_norm + 0.3 * high_wind
    # ② 区域优先 ③ 积聚独立分支 ① NOx 分位门控
    if regional_score >= 0.55:
        return "regional_transport"
    if nox_pct >= 0.75 and traffic_score >= 0.55:
        return "traffic_dominant"
    if accumulation_score >= 0.55:
        return "local_accumulation"
    return "uncertain"


def _effective_config() -> dict:
    """从真实生效的配置源读出判定相关键（不猜、不手写）。"""
    import json as _json
    from agents.source_agent import load_source_rules
    rules = load_source_rules()
    return {
        "judgement_rule": rules.get("judgement_rule", "legacy_priority_chain"),
        "min_strength": rules.get("min_strength"),
        "dominance_margin": rules.get("dominance_margin"),
        "gate_anomaly_min": rules.get("gate_anomaly_min"),
        "traffic_nox_pct_min": rules.get("traffic_nox_pct_min"),
        "signature_weights": rules.get("signature_weights"),
    }


def main() -> int:
    # V9.7：气象走年度磁盘缓存（取数层注入，公式不变），使在线复跑可在
    # 数分钟内完成，且结果不受网络抖动影响。
    try:
        import meteo_cache
        # HERE 是 v9/ 目录；缓存必须落在与捕获脚本一致的 benchmarks/results/ 下，
        # 否则会重新联网取数（V9.7 修正）。
        meteo_cache.install(HERE.parents[0] / "results" / "meteo_cache")
        print("气象：使用年度磁盘缓存", flush=True)
    except ImportError:
        pass
    from agents.air_quality_agent import AirQualityAgent
    from agents import meteorology_agent as met
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.decison_agent import run as decision_run
    from agents.report_agent import ReportAgent
    from agents.traffic_agent import TrafficAgent

    conn = sqlite3.connect(gv6.DB_PATH)
    agents = {"air": AirQualityAgent(), "meteo": met.run, "traffic": TrafficAgent(),
              "spatial": SpatialAgent(), "source": SourceAgent(),
              "decision": decision_run, "report": ReportAgent()}

    all_rows = []
    for station in gv6.STATIONS:
        title = ("一次性盲测年（跨年份留出，运行前未做任何参数改动）"
                 if YEAR != 2024 else "V9 留出年（开发年）")
        print(f"=== {station['name']} / {YEAR}（{title}） ===", flush=True)
        wind = gv6.load_annual_wind(YEAR, station["lat"], station["lon"])
        candidates = gv6.build_windows(conn, station["db_name"], YEAR, wind)
        counts = {}
        for c in candidates:
            counts[c["label"]] = counts.get(c["label"], 0) + 1
        print(f"  候选 {len(candidates)} 个，标注分布 {counts}", flush=True)

        rng = random.Random(YEAR)
        picked = []
        for label, count in SAMPLE_PLAN.items():
            if label == "weekend_negative":
                pool = [c for c in candidates if not c["is_weekday"]]
            else:
                pool = [c for c in candidates if c["label"] == label and c["is_weekday"]]
            rng.shuffle(pool)
            for item in pool[:count]:
                row = dict(item)
                row["sample_group"] = label
                row["station"] = station["name"]
                picked.append(row)
        print(f"  抽样 {len(picked)} 窗（目标 {sum(SAMPLE_PLAN.values())}）", flush=True)

        no2_population = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
            "AND time LIKE ? AND no2 IS NOT NULL", (station["db_name"], f"{YEAR}-%")))
        nox_population = sorted(v for (v,) in conn.execute(
            "SELECT nox FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
            "AND time LIKE ? AND nox IS NOT NULL", (station["db_name"], f"{YEAR}-%")))
        general_o3_p70 = {}
        for (name,) in conn.execute(
                "SELECT DISTINCT station_name FROM air_quality_hourly "
                "WHERE station_type='general' AND time LIKE ?", (f"{YEAR}-%",)):
            o3v = sorted(v for (v,) in conn.execute(
                "SELECT o3 FROM air_quality_hourly WHERE station_name=? AND o3 IS NOT NULL "
                "AND time LIKE ?", (name, f"{YEAR}-%")))
            if o3v:
                general_o3_p70[name] = o3v[int(0.70 * len(o3v))]

        for index, window in enumerate(picked):
            full_task = {"task_id": f"V9_{station['id']}_{window['date']}_{window['start_hour']}",
                         "station_name": station["name"], "station_id": station["id"],
                         "lat": station["lat"], "lon": station["lon"],
                         "start_time": window["start_time"], "end_time": window["end_time"]}
            air = agents["air"].run(dict(full_task))
            meteo = agents["meteo"](dict(full_task))
            traffic = agents["traffic"].run(dict(full_task))
            spatial = agents["spatial"].run(dict(full_task), meteo)
            source = agents["source"].run({"task_id": full_task["task_id"],
                                           "station_name": station["name"]},
                                          air_result=air, meteo_result=meteo,
                                          traffic_result=traffic, spatial_result=spatial)
            sres = source.get("result") or {}
            judgement = (sres.get("main_judgement") or "").lower()
            if "traffic" in judgement:
                c_label = "traffic_dominant"
            elif "regional" in judgement:
                c_label = "regional_transport"
            elif "accumulation" in judgement or "local" in judgement:
                c_label = "local_accumulation"
            else:
                c_label = "uncertain"

            # A 单源规则（与 V5/V6 同口径）
            nox_pct = statistics.mean([1.0 if v < window["mean_nox"] else 0.0
                                       for v in nox_population]) if nox_population else 0.0
            no2_pct = statistics.mean([1.0 if v < window["mean_no2"] else 0.0
                                       for v in no2_population]) if no2_population else 0.0
            if nox_pct >= 0.80:
                a_label = "traffic_dominant"
            elif no2_pct >= 0.80:
                a_label = "local_accumulation"
            else:
                a_label = "uncertain"

            # B 多源规则（与 C 相同的多站同步证据，经 window.sync_count 传入）
            b_label = gv6.method_b_v6(window, station, nox_population, no2_population,
                                      _traffic_conn(), _atc_ids(station), YEAR, wind)
            bplus_label = method_bplus(window, station, nox_population, no2_population, conn)
            label_v2 = gv6.annotate_v2(window, _general_p75(conn, YEAR), conn,
                                       nox_population, no2_population,
                                       window["mean_aqhi"])
            label_v3 = gv6.annotate_v3(window, conn, station["db_name"], YEAR,
                                       general_o3_p70)
            all_rows.append({
                "station": station["name"], "date": window["date"],
                "start_hour": window["start_hour"], "label": window["label"],
                "sample_group": window.get("sample_group"),
                "A": a_label, "B": b_label, "Bplus": bplus_label, "C": c_label,
                "label_v2": label_v2, "label_v3": label_v3,
                "source_judgement": sres.get("main_judgement"),
                "confidence": source.get("confidence"),
            })
            print(f"  [{index+1}/{len(picked)}] {window['date']} {window['start_hour']:02d}时 "
                  f"标注={window['label']:<19} C={c_label}", flush=True)

    conn.close()
    _close_traffic_conn()

    # ── 汇总 ────────────────────────────────────────────────────────────────
    truths = [r["label"] for r in all_rows]
    methods = ("A", "B", "Bplus", "C")
    point = {m: round(macro_f1(truths, [r[m] for r in all_rows]), 4) for m in methods}
    clusters = [r["date"] for r in all_rows]
    # V9.5 口径修正：CI 必须与表格里的 Macro-F1 同口径（V9.1 曾修，V9.4
    # 重跑脚本时被旧版覆盖回"逐窗命中率差"）。统一用 metrics 里的
    # clustered_macro_f1_diff，保证 mean_diff == 点估计差。
    ci = {m: clustered_macro_f1_diff(truths, [r[m] for r in all_rows],
                                     [r["B"] for r in all_rows], clusters, reps=10000)
          for m in ("C", "Bplus")}

    by_station = {}
    for station in gv6.STATIONS:
        subset = [r for r in all_rows if r["station"] == station["name"]]
        by_station[station["name"]] = {m: round(macro_f1(
            [r["label"] for r in subset], [r[m] for r in subset]), 4) for m in methods}

    # 弃判覆盖率—风险曲线
    curves = {m: coverage_risk_curve(truths, [r[m] for r in all_rows]) for m in methods}

    # 弱证据窗口（weekend_negative 组）误报
    weak = [r for r in all_rows if r["sample_group"] == "weekend_negative"]
    weak_traffic = {m: sum(1 for r in weak if r[m] == "traffic_dominant") for m in methods}

    n_labelled_human = 0  # 人工盲评 pending，不伪造
    payload = {
        "design": {
            "holdout_year": YEAR,
            # V9.7：把本次运行实际生效的判定配置写进结果，保证任何指标都能
            # 连同配置一起被复算（此前报告出现"同一名称两种数字"即因缺此记录）。
            "effective_config": _effective_config(),
            "leakage_audit": (f"{YEAR} 未进入任何建模/阈值选择/评分脚本"
                              "（V9.9 谱系审计：results/v99_blind_year_lineage.json）"
                              if YEAR != 2024 else
                              "2024 未进入 V1-V8 任何训练/阈值设计/误差分析（此前仅用 2022/2023）"),
            "run_nature": ("cross-year holdout（跨年份留出），非按时间顺序的未来预测测试"
                           if YEAR != 2024 else "development/holdout year（V9 开发年）"),
            "one_shot": YEAR != 2024,
            "frozen": "运行前未做任何参数改动；产物写入独立文件，不覆盖开发年结果",
            "human_blind_rating": "PENDING — 模板已提供（v9_governance_label_template.csv），未伪造标签",
            "sample_plan": SAMPLE_PLAN,
            "bplus_definition": "B 输入 + V8 结构机制（NOx 分位门控/区域优先/积聚分支），隔离机制价值",
        },
        "point_f1_main_labels": point,
        "point_f1_independent_v2": {m: round(macro_f1(
            [r["label_v2"] for r in all_rows], [r[m] for r in all_rows]), 4) for m in methods},
        "point_f1_independent_v3": {m: round(macro_f1(
            [r["label_v3"] for r in all_rows], [r[m] for r in all_rows]), 4) for m in methods},
        "clustered_ci_vs_B": ci,
        "by_station": by_station,
        "confusion": {m: confusion(truths, [r[m] for r in all_rows]) for m in methods},
        "coverage_risk_curves": curves,
        "weak_evidence_traffic_misfire": weak_traffic,
        "abstention_coverage": {
            m: {"uncertain_rate": round(sum(1 for r in all_rows if r[m] == "uncertain")
                                        / len(all_rows), 4),
                "uncertain_n": sum(1 for r in all_rows if r[m] == "uncertain")}
            for m in methods},
        "human_labels_n": n_labelled_human,
        "label_distribution": dict(__import__("collections").Counter(
            r["label"] for r in all_rows)),
    }
    path = save_result(f"v9_governance{TAG}", payload)
    print(f"\nsaved -> {path}")
    print("=== V9 留出年（2024）主标注 Macro-F1 ===")
    for m in methods:
        print(f"  {m}: {point[m]}  弱证据交通误判 {weak_traffic[m]}/{len(weak)}")
    for m in ("C", "Bplus"):
        if m in ci:
            print(f"  {m}−B 按日期聚类 CI: {ci[m]}")
    print("  分站点:", by_station)
    print("  v2/v3 独立标注:", payload["point_f1_independent_v2"],
          payload["point_f1_independent_v3"])

    # 导出 CSV
    with (HERE / "results" / f"v9_governance_cases{TAG}.csv").open("w", newline="",
                                                             encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    with (HERE / "results" / f"v9_governance_predictions{TAG}.csv").open("w", newline="",
                                                                   encoding="utf-8") as h:
        writer = csv.writer(h)
        writer.writerow(["station", "date", "start_hour", "truth", "method", "prediction"])
        for r in all_rows:
            for m in methods:
                writer.writerow([r["station"], r["date"], r["start_hour"],
                                 r["label"], m, r[m]])
    return 0


_TRAFFIC = {}


def _traffic_conn():
    if "conn" not in _TRAFFIC:
        from tools.traffic_tools import open_db as open_traffic_db
        _TRAFFIC["conn"] = open_traffic_db()
    return _TRAFFIC["conn"]


def _atc_ids(station):
    if station["name"] not in _TRAFFIC:
        from tools.traffic_tools import get_atc_station_ids
        _TRAFFIC[station["name"]] = get_atc_station_ids(station["name"],
                                                        include_nearby=False)
    return _TRAFFIC[station["name"]]


def _close_traffic_conn():
    if "conn" in _TRAFFIC:
        _TRAFFIC["conn"].close()


def _general_p75(conn, year: int) -> dict:
    p75 = {}
    for (name,) in conn.execute(
            "SELECT DISTINCT station_name FROM air_quality_hourly "
            "WHERE station_type='general' AND time LIKE ?", (f"{year}-%",)):
        values = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
            "AND time LIKE ?", (name, f"{year}-%")))
        if values:
            p75[name] = values[int(0.75 * len(values))]
    return p75


if __name__ == "__main__":
    sys.exit(main())
