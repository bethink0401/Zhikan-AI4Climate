"""Calibrate per-profile pollution exchange rates on the dev set only.

Reads the dev-date candidate sets stored in results/travel_multigoal.json
(2023-08-15), searches pollution_exchange_minutes in [8, 20] per profile and
scores each candidate rate with the **independent** criteria (preference
rubric satisfaction, dominated rate, time/intake ratios vs the shortest-time
baseline) — never with the system's own utility score.

The chosen rates are written into config/travel_profile.json (the independent
test date 2023-11-08 is never touched here; re-run bench_travel_multigoal.py
afterwards for the frozen one-shot retest).

Usage: /usr/bin/python3 tests/benchmarks/calibrate_exchange_rates.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE / "config" / "travel_profile.json"
RESULTS_PATH = Path(__file__).resolve().parent / "results" / "travel_multigoal.json"
CALIB_PATH = Path(__file__).resolve().parent / "results" / "exchange_calibration.json"

DATE_DEV = "2023-08-15"
EXCHANGE_MIN, EXCHANGE_MAX = 8.0, 20.0
TIME_RATIO_LIMIT = 1.25


def weighted_utility(option: dict, exchange: float) -> float:
    """用新交换率重算效用（分量来自 route_options 的 utility_detail）。"""
    return (
        option["outdoor_equivalent_minutes"]
        + option["weather_added_minutes"]
        + option["mode_constant_minutes"]
        + option["overrun_minutes"]
        + option["walk_overrun_minutes"]
        + exchange * option["intake_index"]
    )


def pick_with(candidates: list[dict], exchange: float) -> dict:
    return min(candidates, key=lambda o: (weighted_utility(o, exchange),
                                          o["duration_minutes"]))


def pick_shortest(candidates: list[dict]) -> dict:
    return min(candidates, key=lambda o: (o["duration_minutes"], o["intake_index"]))


def is_nondominated(option: dict, candidates: list[dict]) -> bool:
    for other in candidates:
        if other is option:
            continue
        if (other["duration_minutes"] <= option["duration_minutes"]
                and other["intake_index"] <= option["intake_index"]
                and (other["duration_minutes"] < option["duration_minutes"]
                     or other["intake_index"] < option["intake_index"])):
            return False
    return True


def main() -> int:
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    rubric = data["design"]["preference_rubric"]
    dev_rows = [r for r in data["rows"] if r.get("date") == DATE_DEV and r.get("candidates")]

    # ── 基线结构诊断：A/B 是否经常同选、候选集有没有真实权衡 ───────────────
    same_pick = tradeoff = 0
    for row in dev_rows:
        cs = row["candidates"]
        a, b = pick_shortest(cs), min(cs, key=lambda o: (o["intake_index"], o["duration_minutes"]))
        if (a["route_id"], a["mode"]) == (b["route_id"], b["mode"]):
            same_pick += 1
        else:
            tradeoff += 1
    print(f"开发集 A/B 同选率: {same_pick}/{len(dev_rows)}（其余 {tradeoff} 例存在时间-暴露权衡）")

    profiles = sorted({r["profile"] for r in dev_rows})
    chosen: dict[str, float] = {}
    report: dict = {"dev_date": DATE_DEV, "grid": [EXCHANGE_MIN, EXCHANGE_MAX],
                    "time_ratio_limit": TIME_RATIO_LIMIT, "ab_same_pick_rate": f"{same_pick}/{len(dev_rows)}",
                    "per_profile": {}}

    for profile in profiles:
        rows = [r for r in dev_rows if r["profile"] == profile]
        rule = rubric[profile]
        trials = []
        rate = EXCHANGE_MIN
        while rate <= EXCHANGE_MAX + 1e-9:
            pref, dominated, t_ratio, e_ratio = [], [], [], []
            for row in rows:
                cs = row["candidates"]
                t_min = min(c["duration_minutes"] for c in cs)
                e_min = min(c["intake_index"] for c in cs)
                pick = pick_with(cs, rate)
                a = pick_shortest(cs)
                ok = True
                if rule.get("modes") and pick["mode"] not in rule["modes"]:
                    ok = False
                if rule.get("max_time_ratio") and pick["duration_minutes"] > rule["max_time_ratio"] * t_min:
                    ok = False
                if rule.get("max_intake_ratio") and pick["intake_index"] > rule["max_intake_ratio"] * e_min:
                    ok = False
                pref.append(1.0 if ok else 0.0)
                dominated.append(0.0 if is_nondominated(pick, cs) else 1.0)
                t_ratio.append(pick["duration_minutes"] / a["duration_minutes"])
                e_ratio.append(pick["intake_index"] / a["intake_index"])
            trials.append({
                "exchange": round(rate, 1),
                "pref": round(statistics.mean(pref), 4),
                "dominated_rate": round(statistics.mean(dominated), 4),
                "mean_time_ratio_vs_A": round(statistics.mean(t_ratio), 4),
                "mean_intake_ratio_vs_A": round(statistics.mean(e_ratio), 4),
            })
            rate += 1.0

        feasible = [t for t in trials if t["mean_time_ratio_vs_A"] <= TIME_RATIO_LIMIT]
        pool = feasible or trials
        best = min(pool, key=lambda t: (-t["pref"], t["dominated_rate"],
                                        t["mean_intake_ratio_vs_A"]))
        chosen[profile] = best["exchange"]
        report["per_profile"][profile] = {"chosen": best["exchange"], "trials": trials}
        print(f"{profile:<18} -> 交换率 {best['exchange']} min/EU | "
              f"偏好 {best['pref']} 支配率 {best['dominated_rate']} "
              f"时间比 {best['mean_time_ratio_vs_A']} EU比 {best['mean_intake_ratio_vs_A']}")

    # ── 写回 config ────────────────────────────────────────────────────────
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    changed = {}
    for profile in config["profiles"]:
        pid = profile["profile_id"]
        if pid in chosen and float(profile.get("pollution_exchange_minutes", 0)) != chosen[pid]:
            changed[pid] = (profile.get("pollution_exchange_minutes"), chosen[pid])
            profile["pollution_exchange_minutes"] = chosen[pid]
    config["_comment"] = (
        config.get("_comment", "")
        + " 【2026-10-13 校准】pollution_exchange_minutes 按画像在开发集(2023-08-15)上以独立口径"
          "(画像约束满足率/被支配率/时间成本约束)网格搜索 8-20 min/EU 后冻结；独立测试集未参与。"
    )
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    report["config_changes"] = {k: {"old": v[0], "new": v[1]} for k, v in changed.items()}
    CALIB_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nconfig 更新: {report['config_changes']}")
    print(f"标定记录 -> {CALIB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
