# -*- coding: utf-8 -*-
"""V9.5 统计口径审计：从已存逐窗预测重算，不重跑链路。

审计目标（回应审查提出的数字矛盾）：
  1. 治理留出/PIT 的 C−B 差值：报告表格是 Macro-F1 差，而此前 JSON 里的
     CI 用的是"逐窗命中率差"（两个不同的统计量）——本脚本对**同一批预测**
     同时算出两种口径，明确暴露差异，并把 JSON 修正为同口径值；
  2. 弃判覆盖率；
  3. 输出一份可复算的审计台账 v95_stats_audit.json。

用法：/usr/bin/python3 tests/benchmarks/v9/audit_stats.py
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE))

from metrics import clustered_macro_f1_diff, macro_f1  # noqa: E402

RESULTS = BASE / "tests" / "benchmarks" / "results"
V9RESULTS = HERE / "results"
METHODS = ("A", "B", "Bplus", "C")


def load_cases(name: str) -> list[dict]:
    path = V9RESULTS / name
    return list(csv.DictReader(path.open(encoding="utf-8")))


def hit_rate_ci(rows: list[dict], method: str, reps: int = 10000,
                seed: int = 42) -> dict:
    """旧口径：逐窗命中率差（0/1）的聚类 bootstrap——仅供对照。"""
    truths = [r["label"] for r in rows]
    clusters = [r["date"] for r in rows]
    x = [1.0 if r[method] == t else 0.0 for r, t in zip(rows, truths)]
    y = [1.0 if r["B"] == t else 0.0 for r, t in zip(rows, truths)]
    units: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        units.setdefault(c, []).append(i)
    unit_ids = sorted(units)
    rng = random.Random(seed)
    diffs = [a - b for a, b in zip(x, y)]
    samples = sorted(
        statistics.mean(diffs[i] for i in chosen)
        for chosen in ([i for u in (unit_ids[rng.randrange(len(unit_ids))]
                                    for _ in range(len(unit_ids)))
                        for i in units[u]] for _ in range(reps)) if chosen)
    return {"metric": "per_window_hit_rate_diff",
            "mean_diff": round(statistics.mean(diffs), 4),
            "ci95": [round(samples[int(0.025 * reps)], 4),
                     round(samples[int(0.975 * reps)], 4)]}


def audit(name: str) -> dict:
    rows = load_cases(name)
    truths = [r["label"] for r in rows]
    clusters = [r["date"] for r in rows]
    point_raw = {m: macro_f1(truths, [r[m] for r in rows]) for m in METHODS}
    point = {m: round(v, 4) for m, v in point_raw.items()}
    abstention = {m: round(sum(1 for r in rows if r[m] == "uncertain") / len(rows), 4)
                  for m in METHODS}
    ci = {}
    for m in ("C", "Bplus"):
        ci[m] = {
            "correct_macro_f1_diff": clustered_macro_f1_diff(
                truths, [r[m] for r in rows], [r["B"] for r in rows], clusters),
            "legacy_hit_rate_diff": hit_rate_ci(rows, m),
        }
    # 一致性校验：正确口径的 mean_diff 必须等于点估计差（容差 = 4 位取整）
    checks = {}
    for m in ("C", "Bplus"):
        expected = point_raw[m] - point_raw["B"]
        got = ci[m]["correct_macro_f1_diff"]["mean_diff"]
        checks[m] = {"point_estimate_diff": round(expected, 6), "ci_mean_diff": got,
                     "consistent": abs(expected - got) < 1e-4}
    return {"cases_file": name, "n": len(rows), "n_clusters": len(set(clusters)),
            "point_f1_main_labels": point, "abstention_rate": abstention,
            "ci": ci, "consistency_checks": checks}


def main() -> int:
    report = {"note": "V9.5 统计口径审计：同批预测下的正确口径 vs 旧（命中率）口径",
              "holdout_2024": audit("v9_governance_cases.csv"),
              "pit_2024": audit("v9_governance_pit_cases.csv")}

    # 把修正后的 CI + 弃判率写回 v9_governance.json（保留旧值为 _legacy）
    gov_path = RESULTS / "v9_governance.json"
    d = json.loads(gov_path.read_text(encoding="utf-8"))
    ho = report["holdout_2024"]
    d["clustered_ci_vs_B_legacy_hit_rate"] = d.get("clustered_ci_vs_B")
    d["clustered_ci_vs_B"] = {m: ho["ci"][m]["correct_macro_f1_diff"]
                              for m in ("C", "Bplus")}
    d["clustered_ci_vs_B_note"] = (
        "V9.5 修正：CI 与表格 Macro-F1 同口径（clustered_macro_f1_diff）；"
        "旧值（逐窗命中率差）保留在 clustered_ci_vs_B_legacy_hit_rate 供审计。")
    d["abstention_coverage"] = {m: {"uncertain_rate": ho["abstention_rate"][m],
                                    "uncertain_n": round(ho["abstention_rate"][m] * ho["n"])}
                                for m in METHODS}
    gov_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    (HERE / "results" / "v95_stats_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for key in ("holdout_2024", "pit_2024"):
        block = report[key]
        print(f"=== {key}（n={block['n']}，{block['n_clusters']} 日期簇）===")
        print("  Macro-F1:", block["point_f1_main_labels"])
        for m in ("C", "Bplus"):
            good = block["ci"][m]["correct_macro_f1_diff"]
            bad = block["ci"][m]["legacy_hit_rate_diff"]
            print(f"  {m}−B 正确口径: {good['mean_diff']:+.4f} CI {good['ci95']}")
            print(f"  {m}−B 旧口径  : {bad['mean_diff']:+.4f} CI {bad['ci95']}  <-- 已废弃")
        print("  弃判率:", block["abstention_rate"])
        print("  一致性校验:", block["consistency_checks"])
    print("\n审计台账 -> tests/benchmarks/v9/results/v95_stats_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
