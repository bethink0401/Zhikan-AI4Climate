"""Benchmark V4: final round for the multi-objective travel decision.

Pipeline (per the V4 plan):

  1. dev date 2023-10-18 (the v3 report date — it is now the tuning set):
     run the system, then grid-search per-profile pollution exchange rates
     with the Pareto screening active, scoring candidates by the independent
     rubric plus a **balance score** (normalised time + normalised intake
     distance from the candidate-set ideal point);
  2. freeze the chosen rates into config/travel_profile.json;
  3. blind date 2023-11-15 (never seen in any earlier round): one-shot
     evaluation of A shortest-time / B lowest-intake / C multi-objective.

The scenario set is the same pre-registered set as bench_travel_tradeoff.py
(mid-route pollution sources, cross-district case) — fixed before V4 started.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_v4.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from common import save_result  # noqa: E402
from bench_travel_multigoal import (  # noqa: E402
    METHOD_NAMES,
    STATION,
    evaluate_row,
    summarise,
)
from bench_travel_tradeoff import CASES, DATE_REPORT, PROFILES, SLOTS  # noqa: E402

DATE_DEV = DATE_REPORT        # 2023-10-18，本轮的调参集
DATE_BLIND = "2023-11-15"     # 周三，V4 全新盲测日（任何历史轮次未见过）

EXCHANGE_MIN, EXCHANGE_MAX = 8.0, 20.0
TIME_RATIO_LIMIT = 1.25


def weighted_utility(option: dict, exchange: float) -> float:
    return (
        option["outdoor_equivalent_minutes"]
        + option["weather_added_minutes"]
        + option["mode_constant_minutes"]
        + option["overrun_minutes"]
        + option["walk_overrun_minutes"]
        + exchange * option["intake_index"]
    )


def pick_with(candidates: list[dict], exchange: float) -> dict:
    """与系统一致的选取规则：非支配集中效用最优（效用以新交换率重算）。"""
    def dominated(option: dict) -> bool:
        for other in candidates:
            if other is option:
                continue
            if (other["duration_minutes"] <= option["duration_minutes"]
                    and other["intake_index"] <= option["intake_index"]
                    and (other["duration_minutes"] < option["duration_minutes"]
                         or other["intake_index"] < option["intake_index"])):
                return True
        return False

    eligible = [o for o in candidates if not dominated(o)] or candidates
    return min(eligible, key=lambda o: (weighted_utility(o, exchange), o["duration_minutes"]))


def balance_score(option: dict, candidates: list[dict]) -> float:
    """综合平衡度：归一化时间距离 + 归一化吸入距离（越低越均衡）。"""
    t_values = [c["duration_minutes"] for c in candidates]
    e_values = [c["intake_index"] for c in candidates]
    t_span = max(t_values) - min(t_values) or 1.0
    e_span = max(e_values) - min(e_values) or 1.0
    return (
        (option["duration_minutes"] - min(t_values)) / t_span
        + (option["intake_index"] - min(e_values)) / e_span
    )


def satisfies(option: dict, profile: str, rubric: dict, t_min: float, e_min: float) -> bool:
    rule = rubric[profile]
    if rule.get("modes") and option.get("mode") not in rule["modes"]:
        return False
    if rule.get("max_time_ratio") and option["duration_minutes"] > rule["max_time_ratio"] * t_min:
        return False
    if rule.get("max_intake_ratio") and option["intake_index"] > rule["max_intake_ratio"] * e_min:
        return False
    return True


def calibrate_exchange(dev_rows: list[dict], rubric: dict) -> tuple[dict[str, float], list[dict]]:
    """开发集上按画像搜索交换率：偏好满足率优先，其次平衡度，再 EU 比。"""
    chosen: dict[str, float] = {}
    trials_all: dict[str, list[dict]] = {}
    for profile in sorted({r["profile"] for r in dev_rows}):
        rows = [r for r in dev_rows if r["profile"] == profile and r.get("candidates")]
        trials = []
        rate = EXCHANGE_MIN
        while rate <= EXCHANGE_MAX + 1e-9:
            pref, balance, e_ratio, t_ratio = [], [], [], []
            for row in rows:
                cs = row["candidates"]
                t_min = min(c["duration_minutes"] for c in cs)
                e_min = min(c["intake_index"] for c in cs)
                a = min(cs, key=lambda c: (c["duration_minutes"], c["intake_index"]))
                pick = pick_with(cs, rate)
                pref.append(1.0 if satisfies(pick, profile, rubric, t_min, e_min) else 0.0)
                balance.append(balance_score(pick, cs))
                e_ratio.append(pick["intake_index"] / a["intake_index"])
                t_ratio.append(pick["duration_minutes"] / a["duration_minutes"])
            trials.append({
                "exchange": round(rate, 1),
                "pref": round(statistics.mean(pref), 4),
                "mean_balance": round(statistics.mean(balance), 4),
                "mean_intake_ratio_vs_A": round(statistics.mean(e_ratio), 4),
                "mean_time_ratio_vs_A": round(statistics.mean(t_ratio), 4),
            })
            rate += 1.0
        feasible = [t for t in trials if t["mean_time_ratio_vs_A"] <= TIME_RATIO_LIMIT]
        pool = feasible or trials
        best = min(pool, key=lambda t: (-t["pref"], t["mean_balance"],
                                        t["mean_intake_ratio_vs_A"]))
        chosen[profile] = best["exchange"]
        trials_all[profile] = trials
        print(f"  {profile:<18} -> 交换率 {best['exchange']} min/EU | "
              f"偏好 {best['pref']} 平衡度 {best['mean_balance']} "
              f"时间比 {best['mean_time_ratio_vs_A']}", flush=True)
    return chosen, trials_all


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    rubric_source = json.loads(
        (Path(__file__).resolve().parent / "results" / "travel_multigoal.json")
        .read_text(encoding="utf-8"))
    rubric = rubric_source["design"]["preference_rubric"]

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions: dict[str, dict] = {}
    for date in (DATE_DEV, DATE_BLIND):
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"V4_{date}_{slot}_meteo",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"V4_{date}_{slot}_air",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            mres = meteo.get("result") or {}
            ares = air.get("result") or {}
            conditions[f"{date}|{slot}"] = {
                "start": start, "end": end, "meteo": meteo, "air": air,
                "wind_speed": mres.get("mean_wind_speed"),
                "wind_dir": mres.get("dominant_wind_direction"),
                "anomaly": ares.get("anomaly_score"),
            }
            print(f"[{date}|{slot}] 风 {conditions[f'{date}|{slot}']['wind_speed']} m/s @ "
                  f"{conditions[f'{date}|{slot}']['wind_dir']}° | 异常分 "
                  f"{conditions[f'{date}|{slot}']['anomaly']}", flush=True)

    def run_all(date: str) -> list[dict]:
        rows = []
        for slot in SLOTS:
            cond = conditions[f"{date}|{slot}"]
            for case in CASES:
                for profile in PROFILES:
                    task = {
                        "task_id": f"V4_{case['case_id']}_{profile}_{date}_{slot}",
                        "origin": case["origin"], "destination": case["destination"],
                        "start_time": cond["start"], "end_time": cond["end"],
                        "travel_profile": profile,
                        "pollution_source": case["pollution_source"],
                        "air_quality_risk": cond["anomaly"],
                    }
                    t0 = _time.perf_counter()
                    out = travel.run(task, meteorology_result=cond["meteo"],
                                     air_quality_result=cond["air"])
                    run_ms = (_time.perf_counter() - t0) * 1000.0
                    options = (out.get("result") or {}).get("route_options") or []
                    row = evaluate_row(case, profile, slot, date, run_ms, options)
                    row["pollution_source"] = case["pollution_source"]["station_name"]
                    # 平衡度（各方法，基于同一候选集）
                    cs = row.get("candidates") or []
                    for name in METHOD_NAMES:
                        entry = (row.get("methods") or {}).get(name) or {}
                        if "duration_minutes" in entry:
                            pick = next((c for c in cs if c["route_id"] == entry["route_id"]
                                         and c["mode"] == entry["mode"]), None)
                            if pick is not None:
                                entry["balance_score"] = round(balance_score(pick, cs), 4)
                    rows.append(row)
        return rows

    # ── 第 1 步：开发日候选（当前系统状态） + 交换率重标定 ────────────────────
    print("=== 开发日（2023-10-18）候选与交换率重标定 ===", flush=True)
    dev_rows = run_all(DATE_DEV)
    config_path = BASE / "config" / "travel_profile.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    chosen, trials = calibrate_exchange(dev_rows, rubric)
    changed = {}
    for profile in config["profiles"]:
        pid = profile["profile_id"]
        if pid in chosen and float(profile.get("pollution_exchange_minutes", 0)) != chosen[pid]:
            changed[pid] = {"old": profile.get("pollution_exchange_minutes"),
                            "new": chosen[pid]}
            profile["pollution_exchange_minutes"] = chosen[pid]
    config["_comment"] = (
        config.get("_comment", "")
        + f" 【V4 校准 2026-10-13】交换率按画像在开发日 {DATE_DEV} 上以独立口径"
          "（偏好满足率→平衡度→EU 比）网格搜索并冻结；盲测日未参与。"
    )
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"config 更新: {changed}", flush=True)

    # ── 第 2 步：全新盲测日一次性评估 ────────────────────────────────────────
    print(f"\n=== 盲测日（{DATE_BLIND}）一次性评估 ===", flush=True)
    blind_rows = run_all(DATE_BLIND)
    dev_summary = summarise(dev_rows, f"开发日 {DATE_DEV}（调参集）")
    blind_summary = summarise(blind_rows, f"盲测日 {DATE_BLIND}（独立检验）")

    # 平衡度汇总
    def balance_summary(rows: list[dict]) -> dict:
        out = {}
        for name in METHOD_NAMES:
            values = []
            for row in rows:
                entry = (row.get("methods") or {}).get(name) or {}
                if entry.get("balance_score") is not None:
                    values.append(entry["balance_score"])
            out[name] = round(statistics.mean(values), 4) if values else None
        return out

    payload = {
        "design": {
            "note": "V4 终轮：系统改进（长者接驳修复 + Pareto 非支配筛选 + 交换率重标定）后，"
                    "在全新盲测日一次性对比。场景沿用预注册权衡集。",
            "system_changes": [
                "config/mtr_supplement.json：MTR 补充站点与跨海显式邻接（修复 transit 候选缺失）",
                "TravelAgent：Pareto 非支配筛选，推荐只出自非支配集（pareto_dominated 已标记）",
                "交换率按画像在开发日重标定（偏好满足率→平衡度→EU 比）",
            ],
            "dates": {"dev": DATE_DEV, "blind_independent": DATE_BLIND},
            "cases": [c["case_id"] for c in CASES],
            "profiles": PROFILES,
            "exchange_calibration": {"chosen": chosen, "changed": changed,
                                     "trials": trials},
        },
        "dev_summary": dev_summary,
        "blind_summary": blind_summary,
        "balance_summary": {"dev": balance_summary(dev_rows),
                            "blind": balance_summary(blind_rows)},
        "rows": dev_rows + blind_rows,
    }
    path = save_result("travel_v4", payload)
    print(f"\nsaved -> {path}")

    print(f"\n=== 盲测日汇总（{DATE_BLIND}，独立检验）===")
    for name, s in blind_summary["methods"].items():
        print(f"  {name}: 中位耗时={s['median_duration_min']}min 平均EU={s['mean_intake_EU']} "
              f"偏好={s['pref_satisfaction_rate']} 非支配={s['nondominated_rate']}")
    print(f"  A/B 同选率: {blind_summary['ab_same_pick_rate']}")
    print(f"  综合平衡度（越低越均衡）: {payload['balance_summary']['blind']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
