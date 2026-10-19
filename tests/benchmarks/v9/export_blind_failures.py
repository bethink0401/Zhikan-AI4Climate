"""V9.9：把盲测的**全部失败案例**导出（审查第 4 步要求）。

审查原文："在不修改参数的情况下，一次性运行 A/B/B+/智瞰对照，输出 Macro-F1、
覆盖率、类别召回、置信区间和**全部失败案例**。"前面的指标由
`governance_holdout.py` 直接产出；本脚本只负责把逐例明细导成两样东西：

  - `v99_blind<YEAR>_failures.csv`：每个窗口 × 每个方法的预测与真值，**只保留
    至少有一个方法判错的窗口**（标注每个方法是否判错，便于看清"谁错在哪一类"）；
  - `v99_blind<YEAR>_case_matrix.csv`：全部窗口 × 方法的预测矩阵（成功与失败都在，
    用于复算任何指标）。

用法：
    /usr/bin/python3 tests/benchmarks/v9/export_blind_failures.py 2021
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
RESULTS = HERE / "results"
METHODS = ("A", "B", "Bplus", "C")


def main() -> int:
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    src = RESULTS / f"v9_governance_cases_blind{year}.csv"
    if not src.exists():
        print(f"找不到盲测逐例结果：{src}")
        return 1
    rows = list(csv.DictReader(src.open(encoding="utf-8")))

    matrix = RESULTS / f"v99_blind{year}_case_matrix.csv"
    with matrix.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station", "date", "start_hour", "truth", "label_v2",
                         "label_v3", *METHODS, *[f"{m}_correct" for m in METHODS]])
        for row in rows:
            writer.writerow([row["station"], row["date"], row["start_hour"],
                             row["label"], row.get("label_v2"), row.get("label_v3"),
                             *[row[m] for m in METHODS],
                             *[str(row[m] == row["label"]).lower() for m in METHODS]])

    failures = [row for row in rows
                if any(row[m] != row["label"] for m in METHODS)]
    dest = RESULTS / f"v99_blind{year}_failures.csv"
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station", "date", "start_hour", "truth",
                         *[f"{m}_pred" for m in METHODS],
                         *[f"{m}_wrong" for m in METHODS]])
        for row in failures:
            writer.writerow([row["station"], row["date"], row["start_hour"], row["label"],
                             *[row[m] for m in METHODS],
                             *[str(row[m] != row["label"]).lower() for m in METHODS]])

    print(f"逐例总表 {len(rows)} 行 -> {matrix}")
    print(f"失败案例 {len(failures)} 行（占 {len(failures) / len(rows):.1%}）-> {dest}")
    per_method = {m: sum(1 for row in rows if row[m] != row["label"]) for m in METHODS}
    print(f"各方法错判数：{per_method}")

    # 供报告数值块直接读取的小结（避免报告再手写任何计数）
    summary = RESULTS / f"v99_blind{year}_failure_summary.json"
    summary.write_text(json.dumps({
        "year": year, "n_cases": len(rows), "n_failure_rows": len(failures),
        "per_method_wrong": per_method,
        "per_method_wrong_rate": {m: round(per_method[m] / len(rows), 4) for m in METHODS},
        "failure_csv": dest.name, "matrix_csv": matrix.name,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"小结 -> {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
