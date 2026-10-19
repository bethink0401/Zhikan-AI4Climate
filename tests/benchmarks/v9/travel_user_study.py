"""V9 Experiment 3: preparation for the human preference study.

Status per protocol: the USER STUDY ITSELF IS PENDING — no participants have
been recruited in this environment, so no preference/win-rate numbers are
produced here and none may be fabricated. This script prepares everything the
study needs:

  1. Runs the frozen system on a NEW date (2024-03-13, never used in any
     round) over the trade-off OD set, exporting the three anonymised options
     (A fastest-feasible / B lowest-exposure-feasible / C system) per case
     with time, EU and constraint info;
  2. Copies the blind-rating templates from the V9 execution package into
     results/ marked PENDING.

The study design (fixed here, pre-registered): participants first state hard
constraints and preferences (max trip time, walking ability, mode limits,
acceptable time/exposure trade), then rate anonymised A/B/C options shown in
random order with only executable-route info, expected time and relative
exposure; "none of the options" is allowed. Clustering unit: participant x OD.

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_user_study.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time as _time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from bench_travel_multigoal import STATION, _compact  # noqa: E402
from bench_travel_tradeoff import CASES, PROFILES, SLOTS  # noqa: E402

DATE_BLIND = "2024-03-13"   # 周三，V9 全新日期（从未使用）
CAP_RATIO = 1.25
#: 用户研究规模控制：每画像 3 个案例（5 画像 × 3 案例号 = 15 OD 单元）
CASE_STRIDE = 2  # 每隔 2 个案例取一个 → 3 个案例


def pick_fastest_feasible(cands: list[dict], cap: float) -> dict | None:
    feasible = [c for c in cands if c["intake_index"] <= cap]
    return min(feasible, key=lambda c: (c["duration_minutes"], c["intake_index"])) \
        if feasible else None


def pick_lowest_feasible(cands: list[dict], cap: float) -> dict | None:
    feasible = [c for c in cands if c["intake_index"] <= cap]
    return min(feasible, key=lambda c: (c["intake_index"], c["duration_minutes"])) \
        if feasible else None


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{DATE_BLIND} {h0}", f"{DATE_BLIND} {h1}"
        meteo = meteorology_run({"task_id": f"V9T_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"V9T_{slot}_air",
                             "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air,
                            "anomaly": (air.get("result") or {}).get("anomaly_score")}

    selected_cases = CASES[::CASE_STRIDE][:3]
    rows = []
    for slot in SLOTS:
        cond = conditions[slot]
        for case in selected_cases:
            for profile in PROFILES:
                task = {
                    "task_id": f"V9T_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": case["pollution_source"],
                    "air_quality_risk": cond["anomaly"],
                }
                out = travel.run(dict(task), meteorology_result=cond["meteo"],
                                 air_quality_result=cond["air"])
                cands = [_compact(o) for o in (out.get("result") or {})
                         .get("route_options") or []]
                cands = [c for c in cands
                         if isinstance(c["duration_minutes"], float)
                         and isinstance(c["intake_index"], float)]
                if not cands:
                    continue
                cap = CAP_RATIO * min(c["intake_index"] for c in cands)
                out_c = travel.run({**task,
                                    "user_constraints": {"max_intake": round(cap, 4)}},
                                   meteorology_result=cond["meteo"],
                                   air_quality_result=cond["air"])
                cands_c = [_compact(o) for o in (out_c.get("result") or {})
                           .get("route_options") or []]
                c_pick = next((c for c in cands_c if c.get("recommended")), None)
                a_pick = pick_fastest_feasible(cands, cap)
                b_pick = pick_lowest_feasible(cands, cap)

                def brief(pick: dict | None) -> dict | None:
                    if pick is None:
                        return None
                    return {"mode": pick.get("mode"), "route_id": pick.get("route_id"),
                            "duration_minutes": pick["duration_minutes"],
                            "intake_index": pick["intake_index"],
                            "nondominated_hint": None}  # 盲评时不泄露算法名

                rows.append({
                    "case": case["case_id"], "profile": profile, "slot": slot,
                    "depart_time": cond["start"], "cap": round(cap, 4),
                    "n_candidates": len(cands),
                    "A_fastest_feasible": brief(a_pick),
                    "B_lowest_feasible": brief(b_pick),
                    "C_system_constrained": brief(c_pick),
                    "time_spread_min": round(max(c["duration_minutes"] for c in cands)
                                             - min(c["duration_minutes"] for c in cands), 2),
                    "eu_spread": round(max(c["intake_index"] for c in cands)
                                       - min(c["intake_index"] for c in cands), 4),
                })
                print(f"  {slot[:7]} {case['case_id'][:22]:<22} {profile:<17} "
                      f"A={a_pick['duration_minutes'] if a_pick else '-'} "
                      f"B={b_pick['duration_minutes'] if b_pick else '-'} "
                      f"C={c_pick['duration_minutes'] if c_pick else '-'}", flush=True)

    # 汇总三臂差异（供研究设计参考，非用户评价结果）
    diffs = {"A_vs_C_time": [], "B_vs_C_time": [], "A_vs_C_eu": [], "B_vs_C_eu": []}
    for r in rows:
        a, b, c = r["A_fastest_feasible"], r["B_lowest_feasible"], r["C_system_constrained"]
        if a and c:
            diffs["A_vs_C_time"].append(c["duration_minutes"] - a["duration_minutes"])
            diffs["A_vs_C_eu"].append(c["intake_index"] - a["intake_index"])
        if b and c:
            diffs["B_vs_C_time"].append(c["duration_minutes"] - b["duration_minutes"])
            diffs["B_vs_C_eu"].append(c["intake_index"] - b["intake_index"])
    summary = {k: {"mean": round(statistics.mean(v), 4) if v else None,
                   "n": len(v)} for k, v in diffs.items()}

    # 导出推荐表（研究用，算法名不进入盲评材料）
    with (HERE / "results" / "v9_travel_recommendations.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "od_id", "depart_time", "profile",
                         "hard_constraints_json", "option_slot",
                         "mode", "route_id", "duration_minutes", "intake_index"])
        for r in rows:
            for option_slot, key in (("1", "A_fastest_feasible"),
                                     ("2", "B_lowest_feasible"),
                                     ("3", "C_system_constrained")):
                pick = r[key]
                if pick:
                    writer.writerow([r["case"], f"{r['case']}|{r['slot']}",
                                     r["depart_time"], r["profile"],
                                     json.dumps({"max_intake": r["cap"]}),
                                     option_slot, pick["mode"], pick["route_id"],
                                     pick["duration_minutes"], pick["intake_index"]])

    # 复制盲评模板并标记 pending
    package = Path("/Users/chenweiyang/Downloads/%E6%99%BA%E7%9E%B0_V9_%E6%89%A7%E8%A1%8C%E5%8C%85")
    if package.exists():
        for name in ("智瞰_V9_出行偏好盲评模板.csv", "智瞰_V9_治理盲评模板.csv"):
            src = package / name
            if src.exists():
                (HERE / "results" / name).write_text(
                    src.read_text(encoding="utf-8-sig"), encoding="utf-8")
    (HERE / "results" / "USER_STUDY_PENDING.txt").write_text(
        "用户盲评状态：PENDING（未征集参与者）。\n"
        "本目录的 v9_travel_recommendations.csv 为研究材料；在真实参与者完成\n"
        "智瞰_V9_出行偏好盲评模板.csv 之前，不得产生任何胜率/满意度数字。\n"
        f"推荐生成日期：{DATE_BLIND}（从未使用的盲测日期）。\n", encoding="utf-8")

    payload = {
        "design": {
            "blind_date": DATE_BLIND, "cap_ratio": CAP_RATIO,
            "study_status": "PENDING — 无参与者，零伪造数据",
            "study_design": "先锁定硬约束/偏好 → 匿名随机顺序呈现 A/B/C → "
                            "可执行信息+时间+相对暴露评分 → 允许'都不选'；"
                            "聚类单位 = 参与者×OD",
            "meaningful_lift_criterion": "C 相对 B 盲评胜率 ≥ +10 个百分点（预注册）",
        },
        "n_recommendation_rows": len(rows),
        "offline_cost_summary": summary,
        "rows": rows,
    }
    path = save_result("v9_travel", payload)
    print(f"\nsaved -> {path}")
    print(f"推荐行数: {len(rows)}（用户盲评 PENDING，零评价数字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
