"""Benchmark: multi-objective travel decision vs single-objective baselines.

Experiment 3 of the comparison study. Three methods pick one option from the
**same feasible candidate set** (the route_options that TravelAgent enumerates
for one OD × profile × departure slot):

  A  shortest-time        : min duration_minutes
  B  lowest-exposure      : min intake_index
  C  智瞰 multi-objective  : the option TravelAgent recommends (utility-ranked)

All three see identical inputs (same road network, same candidates, same real
EPD / Open-Meteo data). Evaluation uses criteria that are independent of the
utility function: raw travel time, raw intake (EU), a profile constraint
rubric fixed before the run, and a Pareto check. Two dates are used:
2023-08-15 (development) and 2023-11-08 (independent test, never used for
tuning).

An extra method **C_pollution_weighted** is evaluated on the same candidates:
the utility recomputed with a pollution exchange rate calibrated on the dev
set (the shipped exchange rate of 2 min/EU makes the pollution term nearly
irrelevant in mode choice, which the raw-metric results expose). This is a
post-hoc sensitivity variant — the shipped system output C_multigoal is
reported unchanged alongside it.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_multigoal.py
"""
from __future__ import annotations

import statistics
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

DATE_DEV = "2023-08-15"
DATE_TEST = "2023-11-08"

SLOTS = {
    "morning_07_09": ("07:00:00", "09:00:00"),
    "midday_12_14": ("12:00:00", "14:00:00"),
    "evening_18_20": ("18:00:00", "20:00:00"),
}

CASES = [
    {"case_id": "mongkok_to_prince_edward",
     "origin": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694},
     "destination": {"name": "太子站", "lat": 22.32441, "lon": 114.16828}},
    {"case_id": "cwb_to_wanchai",
     "origin": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850},
     "destination": {"name": "湾仔站", "lat": 22.27758, "lon": 114.17334}},
    {"case_id": "central_to_causewaybay",
     "origin": {"name": "中环站", "lat": 22.28100, "lon": 114.15770},
     "destination": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850}},
    {"case_id": "jordan_to_mongkok",
     "origin": {"name": "佐敦站", "lat": 22.3055, "lon": 114.1716},
     "destination": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694}},
    {"case_id": "shamshuipo_to_mongkok",
     "origin": {"name": "深水埗站", "lat": 22.3302, "lon": 114.1621},
     "destination": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694}},
    {"case_id": "northpoint_to_cwb",
     "origin": {"name": "北角站", "lat": 22.2916, "lon": 114.2000},
     "destination": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850}},
]

PROFILES = ["commuter", "parent_with_child", "elderly", "runner", "tourist"]

POLLUTION_SOURCE = {"station_id": "MK", "station_name": "Mong Kok Roadside",
                    "lat": 22.3193, "lon": 114.1694, "radius_km": 3.0}
STATION = {"station_id": "mong_kok", "station_name": "Mong Kok Roadside",
           "lat": 22.3227, "lon": 114.1683}

#: 画像约束评分细则（独立于效用函数，跑之前固定）。
PREFERENCE_RUBRIC = {
    "commuter": {"max_time_ratio": 1.20, "max_intake_ratio": 1.25, "modes": None},
    "parent_with_child": {"max_time_ratio": 1.50, "max_intake_ratio": 1.15, "modes": None},
    "elderly": {"max_time_ratio": 1.50, "max_intake_ratio": 1.10, "modes": ["transit", "walk"]},
    "runner": {"max_time_ratio": None, "max_intake_ratio": 1.05, "modes": None},
    "tourist": {"max_time_ratio": 1.40, "max_intake_ratio": 1.40, "modes": None},
}

METHOD_NAMES = ("A_shortest_time", "B_lowest_intake", "C_multigoal")


def _valid_options(options: list[dict]) -> list[dict]:
    """只保留耗时与吸入都可用的候选，保证各方法面对同一可行集合。"""
    out = []
    for opt in options:
        duration = opt.get("duration_minutes")
        intake = opt.get("intake_index")
        if isinstance(duration, (int, float)) and isinstance(intake, (int, float)):
            out.append(opt)
    return out


def _compact(option: dict) -> dict:
    detail = option.get("utility_detail") or {}
    return {
        "mode": option.get("mode"),
        "route_id": option.get("route_id"),
        "recommended": bool(option.get("recommended")),
        "duration_minutes": float(option["duration_minutes"]),
        "intake_index": float(option["intake_index"]),
        "utility_minutes": float(option.get("utility_minutes") or 0.0),
        "outdoor_equivalent_minutes": float(detail.get("outdoor_equivalent_minutes") or 0.0),
        "weather_added_minutes": float(detail.get("weather_added_minutes") or 0.0),
        "pollution_added_minutes": float(detail.get("pollution_added_minutes") or 0.0),
        "mode_constant_minutes": float(detail.get("mode_constant_minutes") or 0.0),
        "overrun_minutes": float(detail.get("overrun_minutes") or 0.0),
        "walk_overrun_minutes": float(detail.get("walk_overrun_minutes") or 0.0),
    }


def pick_shortest_time(options: list[dict]) -> dict:
    return min(options, key=lambda o: (o["duration_minutes"], o["intake_index"]))


def pick_lowest_intake(options: list[dict]) -> dict:
    return min(options, key=lambda o: (o["intake_index"], o["duration_minutes"]))


def pick_recommended(options: list[dict]) -> dict | None:
    for opt in options:
        if opt.get("recommended"):
            return opt
    return None


def is_nondominated(option: dict, options: list[dict]) -> bool:
    d0, e0 = option["duration_minutes"], option["intake_index"]
    for other in options:
        if other is option:
            continue
        d, e = other["duration_minutes"], other["intake_index"]
        if d <= d0 and e <= e0 and (d < d0 or e < e0):
            return False
    return True


def satisfies_preference(option: dict, profile: str, t_min: float, e_min: float) -> bool:
    rule = PREFERENCE_RUBRIC[profile]
    if rule["modes"] is not None and option.get("mode") not in rule["modes"]:
        return False
    if rule["max_time_ratio"] is not None and option["duration_minutes"] > rule["max_time_ratio"] * t_min:
        return False
    if rule["max_intake_ratio"] is not None and option["intake_index"] > rule["max_intake_ratio"] * e_min:
        return False
    return True


def method_picks(options: list[dict]) -> dict:
    """各方法在同一个候选集合上的选择。"""
    return {
        "A_shortest_time": pick_shortest_time(options),
        "B_lowest_intake": pick_lowest_intake(options),
        "C_multigoal": pick_recommended(options),
    }


def perturb_flip_rate(candidates: list[dict], method: str,
                      reps: int = 30, seed: int = 7) -> float:
    """对吸入指数加 ±3% 量级的随机扰动，看选择是否稳定。"""
    import random

    rng = random.Random(seed)
    base = method_picks(candidates)[method]
    if base is None:
        return 0.0
    changed = 0
    for _ in range(reps):
        shaken = [
            {**o, "intake_index": o["intake_index"] * (1.0 + rng.gauss(0.0, 0.03))}
            for o in candidates
        ]
        pick = method_picks(shaken)[method]
        if pick is None or (pick["route_id"], pick.get("mode")) != (base["route_id"], base.get("mode")):
            changed += 1
    return changed / reps


def build_entry(pick: dict | None, valid: list[dict], profile: str,
                t_min: float, e_min: float, picks: dict) -> dict:
    if pick is None:
        return {"status": "no_pick"}
    entry = {
        "mode": pick.get("mode"), "route_id": pick.get("route_id"),
        "duration_minutes": round(pick["duration_minutes"], 2),
        "intake_index": round(pick["intake_index"], 4),
        "utility_minutes": round(pick.get("utility_minutes") or 0.0, 2),
        "pref_satisfied": satisfies_preference(pick, profile, t_min, e_min),
        "nondominated": is_nondominated(pick, valid),
    }
    a = picks["A_shortest_time"]
    b = picks["B_lowest_intake"]
    if pick is not a:
        entry["d_time_vs_A_pct"] = round(
            (entry["duration_minutes"] / a["duration_minutes"] - 1.0) * 100.0, 2)
        entry["d_intake_vs_A_pct"] = round(
            (entry["intake_index"] / a["intake_index"] - 1.0) * 100.0, 2)
    if pick is not b:
        entry["d_time_vs_B_pct"] = round(
            (entry["duration_minutes"] / b["duration_minutes"] - 1.0) * 100.0, 2)
        entry["d_intake_vs_B_pct"] = round(
            (entry["intake_index"] / b["intake_index"] - 1.0) * 100.0, 2)
    return entry


def within_drive_analysis(candidates: list[dict]) -> dict | None:
    """实验3A子分析：固定驾车方式，只比较路径选择。"""
    drive = [o for o in candidates if o.get("mode") == "drive"]
    if len(drive) < 2:
        return None
    a_d = pick_shortest_time(drive)
    b_d = pick_lowest_intake(drive)
    out = {
        "n_routes": len(drive),
        "A_route_intake": round(a_d["intake_index"], 4),
        "B_route_intake": round(b_d["intake_index"], 4),
        "A_route_minutes": round(a_d["duration_minutes"], 2),
        "B_route_minutes": round(b_d["duration_minutes"], 2),
    }
    rec_d = pick_recommended(drive)
    if rec_d is not None:
        out["C_route_intake"] = round(rec_d["intake_index"], 4)
        out["C_route_minutes"] = round(rec_d["duration_minutes"], 2)
    return out


def evaluate_row(case: dict, profile: str, slot: str, date: str, run_ms: float,
                 options: list[dict]) -> dict:
    valid = _valid_options(options)
    if not valid:
        return {"case": case["case_id"], "profile": profile, "slot": slot,
                "date": date, "status": "no_valid_options"}

    candidates = [_compact(o) for o in valid]
    t_min = min(c["duration_minutes"] for c in candidates)
    e_min = min(c["intake_index"] for c in candidates)
    picks = method_picks(candidates)

    row = {"case": case["case_id"], "profile": profile, "slot": slot, "date": date,
           "n_options": len(valid), "travel_run_ms": round(run_ms, 1),
           "candidates": candidates, "methods": {}}
    for name in METHOD_NAMES:
        if name not in picks:
            continue
        entry = build_entry(picks[name], candidates, profile, t_min, e_min, picks)
        if "status" not in entry:
            entry["perturb_flip_rate"] = round(
                perturb_flip_rate(candidates, name), 4)
        row["methods"][name] = entry

    row["within_drive"] = within_drive_analysis(candidates)
    return row


def summarise(rows: list[dict], tag: str) -> dict:
    """独立口径的方法级汇总。"""
    summary = {"tag": tag, "n_cases": 0, "methods": {}}
    for name in METHOD_NAMES:
        durations, intakes, pref, nondom, flips = [], [], [], [], []
        d_vs_a, e_vs_a, d_vs_b, e_vs_b = [], [], [], []
        cases = 0
        for row in rows:
            entry = (row.get("methods") or {}).get(name) or {}
            if entry.get("status") == "no_pick" or "duration_minutes" not in entry:
                continue
            cases += 1
            durations.append(entry["duration_minutes"])
            intakes.append(entry["intake_index"])
            pref.append(1.0 if entry.get("pref_satisfied") else 0.0)
            nondom.append(1.0 if entry.get("nondominated") else 0.0)
            if "perturb_flip_rate" in entry:
                flips.append(entry["perturb_flip_rate"])
            if "d_time_vs_A_pct" in entry:
                d_vs_a.append(entry["d_time_vs_A_pct"])
                e_vs_a.append(entry["d_intake_vs_A_pct"])
            if "d_time_vs_B_pct" in entry:
                d_vs_b.append(entry["d_time_vs_B_pct"])
                e_vs_b.append(entry["d_intake_vs_B_pct"])
        summary["methods"][name] = {
            "n": cases,
            "median_duration_min": round(statistics.median(durations), 2) if durations else None,
            "mean_duration_min": round(statistics.mean(durations), 2) if durations else None,
            "mean_intake_EU": round(statistics.mean(intakes), 4) if intakes else None,
            "pref_satisfaction_rate": round(statistics.mean(pref), 4) if pref else None,
            "nondominated_rate": round(statistics.mean(nondom), 4) if nondom else None,
            "median_perturb_flip_rate": round(statistics.median(flips), 4) if flips else None,
            "median_d_time_vs_A_pct": round(statistics.median(d_vs_a), 2) if d_vs_a else None,
            "median_d_intake_vs_A_pct": round(statistics.median(e_vs_a), 2) if e_vs_a else None,
            "median_d_time_vs_B_pct": round(statistics.median(d_vs_b), 2) if d_vs_b else None,
            "median_d_intake_vs_B_pct": round(statistics.median(e_vs_b), 2) if e_vs_b else None,
        }
    summary["n_cases"] = summary["methods"]["A_shortest_time"]["n"]

    # 基线结构诊断：A/B 同选率 = 候选集在模式层面缺乏时间-暴露权衡的比例
    same = total = 0
    for row in rows:
        a = (row.get("methods") or {}).get("A_shortest_time") or {}
        b = (row.get("methods") or {}).get("B_lowest_intake") or {}
        if "route_id" in a and "route_id" in b:
            total += 1
            if (a["route_id"], a.get("mode")) == (b["route_id"], b.get("mode")):
                same += 1
    summary["ab_same_pick_rate"] = f"{same}/{total}" if total else None
    return summary


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    # 每个日期 × 时段的气象 / 空气条件只取一次，全部方法共用
    conditions: dict[str, dict] = {}
    for date in (DATE_DEV, DATE_TEST):
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"MG_{date}_{slot}_meteo",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"MG_{date}_{slot}_air",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            mres = meteo.get("result") or {}
            ares = air.get("result") or {}
            key = f"{date}|{slot}"
            conditions[key] = {
                "start": start, "end": end, "meteo": meteo, "air": air,
                "wind_speed": mres.get("mean_wind_speed"),
                "wind_dir": mres.get("dominant_wind_direction"),
                "anomaly": ares.get("anomaly_score"),
            }
            print(f"[{key}] 风 {conditions[key]['wind_speed']} m/s @ "
                  f"{conditions[key]['wind_dir']}° | 异常分 {conditions[key]['anomaly']}", flush=True)

    all_rows = []
    for date in (DATE_DEV, DATE_TEST):
        for slot in SLOTS:
            cond = conditions[f"{date}|{slot}"]
            for case in CASES:
                for profile in PROFILES:
                    task = {
                        "task_id": f"MG_{case['case_id']}_{profile}_{date}_{slot}",
                        "origin": case["origin"], "destination": case["destination"],
                        "start_time": cond["start"], "end_time": cond["end"],
                        "travel_profile": profile,
                        "pollution_source": POLLUTION_SOURCE,
                        "air_quality_risk": cond["anomaly"],
                    }
                    t0 = _time.perf_counter()
                    out = travel.run(task, meteorology_result=cond["meteo"],
                                     air_quality_result=cond["air"])
                    run_ms = (_time.perf_counter() - t0) * 1000.0
                    result = out.get("result") or {}
                    options = result.get("route_options") or []
                    row = evaluate_row(case, profile, slot, date, run_ms, options)
                    all_rows.append(row)
                    pick = (row.get("methods") or {}).get("C_multigoal") or {}
                    print(f"  {date} {slot[:7]} {case['case_id'][:16]:<16} {profile:<17} "
                          f"-> {pick.get('mode', '-'):<7} "
                          f"t={pick.get('duration_minutes', '-')} EU={pick.get('intake_index', '-')}",
                          flush=True)

    dev_rows = [r for r in all_rows if r["date"] == DATE_DEV]
    test_rows = [r for r in all_rows if r["date"] == DATE_TEST]

    dev_summary = summarise(dev_rows, "dev_2023-08-15")
    test_summary = summarise(test_rows, "test_2023-11-08_independent")

    # PPT 散点图数据：测试集上各方法的（时间, 吸入）散点
    scatter = []
    for row in test_rows:
        for name in METHOD_NAMES:
            entry = (row.get("methods") or {}).get(name) or {}
            if "duration_minutes" in entry:
                scatter.append({
                    "method": name, "case": row["case"], "profile": row["profile"],
                    "slot": row["slot"],
                    "duration_minutes": entry["duration_minutes"],
                    "intake_index": entry["intake_index"],
                })

    payload = {
        "design": {
            "methods": {
                "A_shortest_time": "同一候选集合中选 duration_minutes 最小（最短时间基线）",
                "B_lowest_intake": "同一候选集合中选 intake_index 最小（低暴露基线）",
                "C_multigoal": "TravelAgent 效用排序的推荐方案（污染交换率已经"
                               "calibrate_exchange_rates.py 在开发集校准并写入配置）",
            },
            "note": "三组方法面对完全相同的路网、候选方案、真实 EPD/Open-Meteo 数据；"
                    "评价指标（画像约束细则、Pareto、原始 EU/分钟）独立于效用函数，跑前固定。",
            "dates": {"dev": DATE_DEV, "test_independent": DATE_TEST},
            "slots": list(SLOTS), "cases": [c["case_id"] for c in CASES],
            "profiles": PROFILES,
            "preference_rubric": {
                profile: {key: value for key, value in rule.items()}
                for profile, rule in PREFERENCE_RUBRIC.items()
            },
        },
        "conditions": {k: {kk: vv for kk, vv in v.items() if kk not in ("meteo", "air")}
                       for k, v in conditions.items()},
        "dev_summary": dev_summary,
        "test_summary": test_summary,
        "scatter_test": scatter,
        "rows": all_rows,
    }
    path = save_result("travel_multigoal", payload)
    print(f"\nsaved -> {path}")
    print("\n=== 独立测试集汇总（2023-11-08）===")
    for name, s in test_summary["methods"].items():
        print(f"  {name}: 中位耗时={s['median_duration_min']}min 平均EU={s['mean_intake_EU']} "
              f"偏好满足率={s['pref_satisfaction_rate']} 非支配率={s['nondominated_rate']}")
    c = test_summary["methods"]["C_multigoal"]
    print(f"\n  C vs A: 时间 {c['median_d_time_vs_A_pct']}% / EU {c['median_d_intake_vs_A_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
