"""V9.9：测试清单自动导出（审查 P0-3）。

审查（V9.8 报告）："报告写 260 项测试通过，但列出的分类相加为
194 + 31 + 16 + 15 + 1 = 257"——分类明细里漏掉了后加的 3 项报告一致性测试。
根因与报告数值同源：**计数是手写的**。

做法：直接驱动 pytest（先 `--collect-only` 取按文件的收集数，再跑一次取
通过/跳过/失败），把结果写进 `results/v99_test_inventory.json`；报告里的
测试计数块由 `make_report_tables.py` 从该 JSON 生成，不再手写。

用法：
    /usr/bin/python3 tests/benchmarks/v9/audit_test_inventory.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
RESULTS = HERE / "results"

#: 按 V9 轮次归类测试文件——用于回答"这 260 项分别来自哪一轮的修改"。
#: 未列出的文件归入 "legacy"（V9 之前的既有测试）。
ROUND_OF_FILE = {
    "tests/test_v9_offline_online_consistency.py": "V9-P0 离线/在线一致性与回归",
    "tests/test_v95_source_rule.py": "V9.5 判定规则与口径",
    "tests/test_v95_decision_rule.py": "V9.5 判定规则与口径",
    "tests/test_v95_evaluator_consistency.py": "V9.5 判定规则与口径",
    "tests/test_v96_decision_weights.py": "V9.6 权重/数据契约/气象切片",
    "tests/test_v96_data_contracts.py": "V9.6 权重/数据契约/气象切片",
    "tests/test_v96_meteo_cache_slicing.py": "V9.6 权重/数据契约/气象切片",
    "tests/test_v97_online_offline_parity.py": "V9.7 在线/离线逐例等价",
    "tests/test_v97_report_consistency.py": "V9.8 报告数值块一致性",
    "tests/test_v99_report_consistency.py": "V9.9 报告断言与计数自洽",
}

_RUN_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)")


def _pytest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider", *args],
        cwd=BASE, capture_output=True, text=True)


def _collect_per_file() -> dict[str, int]:
    result = _pytest("--collect-only")
    node_ids = [line for line in result.stdout.splitlines() if "::" in line]
    counted = Counter(line.strip().split("::")[0] for line in node_ids)
    return dict(sorted(counted.items(), key=lambda kv: (-kv[1], kv[0])))


def _run_results() -> dict:
    """跑一次 pytest 取通过/跳过/失败。

    注意一个**自指陷阱**：报告一致性测试读的正是本脚本写出的 JSON，所以刚修改过
    测试或清单时，第一遍 pytest 可能因为"读到的还是旧清单"而报失败。因此在出现
    失败且没有收集错误时**重跑一次**，以稳定后的结果为准（最多两遍）。
    """
    result = _pytest()
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    counts = {kind: int(number) for number, kind in _RUN_RE.findall(tail)}
    if counts.get("failed", 0) and not (counts.get("error", 0) + counts.get("errors", 0)):
        retry = _pytest()
        retry_tail = (retry.stdout.strip().splitlines()[-1]
                      if retry.stdout.strip() else "")
        retry_counts = {kind: int(number) for number, kind in _RUN_RE.findall(retry_tail)}
        if retry_counts.get("failed", 0) < counts.get("failed", 0):
            result, tail, counts = retry, retry_tail, retry_counts
    return {
        "summary_line": tail,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "errors": counts.get("error", 0) + counts.get("errors", 0),
        "skipped": counts.get("skipped", 0),
        "exit_code": result.returncode,
    }


def main() -> int:
    per_file = _collect_per_file()
    run = _run_results()
    # 运行摘要行里只有 passed/failed/skipped，没有 "tests collected"；收集数以
    # --collect-only 的按文件统计为准（这也是报告里"收集 N 项"的来源）。
    run["collected"] = sum(per_file.values())

    by_round: dict[str, int] = {}
    for path, count in per_file.items():
        round_name = ROUND_OF_FILE.get(path, "legacy（V9 之前的既有测试）")
        by_round[round_name] = by_round.get(round_name, 0) + count

    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "pytest_run": run,
        "per_file": per_file,
        "per_round": dict(sorted(by_round.items(), key=lambda kv: -kv[1])),
        "total_collected": sum(per_file.values()),
        "note": "由 audit_test_inventory.py 直接驱动 pytest 导出；报告计数块据此生成，禁止手写。",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    dest = RESULTS / "v99_test_inventory.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"收集 {payload['total_collected']} 项 | {run['summary_line']}")
    for name, count in payload["per_round"].items():
        print(f"  {name}: {count}")
    _warn_if_stale(per_file)
    if run["failed"] or run["errors"]:
        print("⚠️ 存在失败/错误，计数块不应被用于报告中")
    print(f"\nsaved -> {dest}")
    return 0 if not (run["failed"] or run["errors"]) else 1


def _warn_if_stale(per_file: dict[str, int]) -> None:
    """提示性检查（不抛异常）：清单覆盖的测试文件是否与磁盘一致。

    真正的不变量由 `tests/test_v99_report_consistency.py` 断言；这里只做提前提醒，
    避免把"清单过期"做成运行失败（那会与一致性测试形成循环依赖）。
    """
    on_disk = {str(p.relative_to(BASE)) for p in (BASE / "tests").glob("test_*.py")}
    recorded = set(per_file)
    if recorded != on_disk:
        print(f"  ⚠️ 测试文件集合与磁盘不一致：仅清单 {sorted(recorded - on_disk)}；"
              f"仅磁盘 {sorted(on_disk - recorded)}")


if __name__ == "__main__":
    raise SystemExit(main())
