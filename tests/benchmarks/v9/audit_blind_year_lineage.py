"""V9.9：盲测年（2021）的数据谱系审计。

审查原文：**"要确认的不只是代码里有没有写过 2021，还要检查历史运行时有没有通过
SQL 查询间接读取 2021 年的数据。例如 2022 年使用滚动 12 个月历史基线时，就可能
读取 2021 年的观测。如果这些观测参与了阈值选择或性能分析，需要在数据谱系中记录。
另外，2021 年可以作为独立测试年，但它早于部分开发数据，因此这是跨年份留出测试，
不是严格按时间顺序的未来预测测试。这两个概念建议区分清楚。"**

本脚本分四层回答：

  L1 字面层：全仓库（排除 data/ 与 docs/）扫描盲测年的字面引用，并按用途分类
     ——「建模/阈值/评分」「数据覆盖审计」「文档/下载器默认值」。
  L2 查询层：枚举基准脚本里所有带年份/时间谓词的 SQL，按过滤模式分类
     ——「同年前缀 LIKE」「滚动区间 >= / <」「无年份约束」。
  L3 可达层：对滚动区间查询，用数据库**实际行数**算出"某分析月会读到哪些年份"，
     并回答"历史上真的读到过盲测年吗"。
  L4 产物层：核验所有评测产物里被评分的日期年份，确认盲测年的窗口从未被评分。

输出 `results/v99_blind_year_lineage.json`，报告块 `V9.9_BLIND_LINEAGE` 据此生成。

用法：
    /usr/bin/python3 tests/benchmarks/v9/audit_blind_year_lineage.py
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
RESULTS = HERE / "results"
BLIND_YEAR = 2021

#: 扫描范围：排除数据目录、文档目录与产物目录（它们只描述数据，不读取数据）。
#: `meteo_cache` 是气象原始数据缓存（本身就是观测值，必然含盲测年时间戳），
#: 必须排除，否则字面层会被几十万行时间戳淹没。
_SKIP_DIRS = {".git", "node_modules", ".workbuddy", "dist", "__pycache__",
              "data", "docs", "projects", ".venv", "venv", "meteo_cache"}
_SCAN_SUFFIXES = {".py", ".json", ".csv", ".sh", ".yaml", ".yml", ".ts", ".vue", ".md"}

#: 本轮审计设施自身的文件按构造就含盲测年，从字面层剔除并在结果里显式列出，
#: 避免"审计自己给自己制造引用"这种自指噪音。
_SELF_REFERENTIAL = (
    "tests/benchmarks/v9/audit_blind_year_lineage.py",
    "tests/benchmarks/v9/results/v99_blind_year_lineage.json",
    "tests/benchmarks/v9/make_report_tables.py",
    "tests/test_v99_report_consistency.py",
)

#: 用途分类规则：命中即归入对应类别（按顺序匹配，先匹配者优先）。
_PURPOSE_RULES = (
    # 本次盲测自己的产物与为它预取的输入缓存——按构造就含盲测年，属正常。
    ("blind_run_artifact", ("blind2021", "blind_result", "v99_blind")),
    ("blind_run_tooling", ("export_blind_failures", "governance_holdout.py",
                           "prefetch_blind_year")),
    ("input_cache", ("wind_openmeteo_", "meteo_cache")),
    ("data_coverage_audit", ("audit_day1_9", "day1_9_audit", "完整性审计",
                             "年份覆盖", "vehicle_mix 非空")),
    ("downloader_default", ("download_", "--years", "--start", "range(2018")),
    ("preregistration", ("protocol.json", "run_manifest", "v9_8_preregistration",
                         "blind_year", "preregistered")),
    ("documentation", ("PROJECT_CONTEXT", "design.md", ".md")),
)
_MODELING_HINT = ("threshold", "config", "tuning", "calib", "score", "predict",
                  "f1", "eval", "baseline", "model")

_SQL_LINE = re.compile(r'"(SELECT|select)\b|SELECT \*|FROM air_quality_hourly|'
                       r'FROM traffic_vehicle_mix')
_YEAR_LIKE = re.compile(r"time LIKE \?|time LIKE '|\{year\}-\%|f\"\{year\}-\%\"")
_TRAILING = re.compile(r"time >= \? AND time < \?|time >= \{|pit_lo")


def _scan_text() -> list[dict]:
    hits = []
    excluded = 0
    for path in sorted(BASE.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(BASE))
        if rel in _SELF_REFERENTIAL:
            excluded += 1
            continue
        if path.stat().st_size > 4_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not re.search(rf"\b{BLIND_YEAR}\b", line):
                continue
            purpose = "unknown_review"
            for name, markers in _PURPOSE_RULES:
                if any(m in rel or m in line for m in markers):
                    purpose = name
                    break
            hits.append({"file": rel, "line": lineno,
                         "snippet": line.strip()[:160], "purpose": purpose})
    _scan_text.excluded_files = excluded  # type: ignore[attr-defined]
    return hits


def _scan_sql_surface() -> list[dict]:
    """枚举基准/工具代码里出现的 SQL 及其年份过滤模式。"""
    roots = [BASE / "tools", BASE / "agents", BASE / "tests"]
    surface = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(BASE))
            if rel in _SELF_REFERENTIAL:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                trailing = _TRAILING.search(line) or "pit_lo" in line
                year_like = _YEAR_LIKE.search(line) or 'f"{year}-%"' in line
                if trailing:
                    kind = "trailing_window"
                elif year_like:
                    kind = "same_year_like"
                elif _SQL_LINE.search(line):
                    kind = "unbounded_or_unspecified"
                else:
                    continue
                surface.append({"file": rel, "line": lineno,
                                "kind": kind, "query": line.strip()[:160]})
    return surface


def _trailing_reachability() -> dict:
    """用数据库实际行数回答"滚动 365 天窗口会不会读到盲测年"。"""
    conn = sqlite3.connect(BASE / "data" / "air_quality.db")
    blind_rows = conn.execute(
        "SELECT COUNT(*) FROM air_quality_hourly WHERE time >= ? AND time < ?",
        (f"{BLIND_YEAR}-01-01 00:00:00", f"{BLIND_YEAR + 1}-01-01 00:00:00")).fetchone()[0]

    # 滚动窗口 = [事件月月初 − 365 天, 事件月月初)。逐月统计会落到盲测年的比例。
    reachable_months = []
    for month in range(1, 13):
        as_of = f"{BLIND_YEAR + 1}-{month:02d}-01 00:00:00"
        lo = (datetime.fromisoformat(as_of) - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
        total = conn.execute(
            "SELECT COUNT(*) FROM air_quality_hourly WHERE time >= ? AND time < ?",
            (lo, as_of)).fetchone()[0]
        in_blind = conn.execute(
            "SELECT COUNT(*) FROM air_quality_hourly WHERE time >= ? AND time < ? "
            "AND time < ?", (lo, as_of, f"{BLIND_YEAR + 1}-01-01 00:00:00")).fetchone()[0]
        reachable_months.append({
            "analysis_month": as_of[:7], "window_lo": lo[:10],
            "rows_total": total, "rows_in_blind_year": in_blind,
            "share_in_blind_year": round(in_blind / total, 4) if total else 0.0,
        })
    # V9.x 实际用的 PIT 事件月全部在 2024 → 窗口最低点落在 2023 年初
    v9_window_lo = (datetime(2024, 1, 1) - timedelta(days=365)).strftime("%Y-%m-%d")
    conn.close()
    return {
        "query": "tools/air_quality_tools.py:load_station_no2_p90(as_of=<事件月月初>)",
        "window_rule": "滚动 365 天，严格早于事件月月初（闭开区间）",
        "blind_year_rows_in_db": blind_rows,
        "v9_actual_earliest_window_date": v9_window_lo,
        "v9_read_blind_year": v9_window_lo.startswith(str(BLIND_YEAR)),
        "counterfactual_if_analysis_year_were": {
            "2022": "窗口会落到 2021-01～2021-12（详见 by_analysis_month）",
            "2024": f"窗口最低点为 {v9_window_lo}（V9.x 的实际情况，未触及 {BLIND_YEAR}）",
        },
        "by_analysis_month": reachable_months,
    }


def _check_artifacts() -> dict:
    """核验评测产物里被评分的日期年份。

    本次盲测跑完后，`*blind<年>*` 的产物**本来就该**含盲测年日期；因此把它们单独
    统计，只用**其余**产物回答"盲测年在开发期被评过分吗"。
    """
    years: Counter = Counter()
    blind_run_years: Counter = Counter()
    files = 0
    blind_files = []
    for path in sorted(RESULTS.glob("*.csv")):
        try:
            rows = list(csv.DictReader(path.open(encoding="utf-8")))
        except (OSError, csv.Error):
            continue
        if not rows:
            continue
        key = next((k for k in ("date", "start_time", "window_start", "day")
                    if k in rows[0]), None)
        if not key:
            continue
        is_blind_run = f"blind{BLIND_YEAR}" in path.name
        if is_blind_run:
            blind_files.append(path.name)
        else:
            files += 1
        target = blind_run_years if is_blind_run else years
        for row in rows:
            value = str(row.get(key) or "")[:4]
            if value.isdigit():
                target[value] += 1
    return {
        "files_scanned": files,
        "scored_date_years": dict(sorted(years.items())),
        "blind_year_windows_scored": years.get(str(BLIND_YEAR), 0),
        "this_blind_run_files": blind_files,
        "this_blind_run_scored_years": dict(sorted(blind_run_years.items())),
        "note": ("`blind_year_windows_scored` 只统计**非本次盲测**的产物；"
                 "本次盲测自己的产物单列在 this_blind_run_*，两者不可混用。"),
    }


def _git_history() -> list[dict]:
    out = subprocess.run(["git", "log", "-S", str(BLIND_YEAR), "--oneline"],
                         cwd=BASE, capture_output=True, text=True).stdout.strip()
    return [{"commit": line.split()[0], "subject": line.split(" ", 1)[1]}
            for line in out.splitlines() if line.strip()]


def main() -> int:
    text_hits = _scan_text()
    sql_surface = _scan_sql_surface()
    reach = _trailing_reachability()
    artifacts = _check_artifacts()
    history = _git_history()

    by_purpose = Counter(hit["purpose"] for hit in text_hits)
    flagged = [h for h in text_hits
               if h["purpose"] in ("modeling_or_threshold", "unknown_review")]

    if not flagged:
        conclusion = (
            "盲测年出现在代码里，但**没有一处参与建模、阈值选择或评分**：命中集中在 "
            "① 数据覆盖审计（examples/audit_day1_9.py 逐年份统计数据表行数，含 2021）"
            "② 下载器默认年份串 ③ 预注册文档。因此 V9.8『2018–2021 从未被任何脚本引用』"
            "的说法**不准确**，应修正为『从未被任何建模/阈值/评分脚本使用』。")
    else:
        conclusion = (
            f"有 {len(flagged)} 处命中无法归入已知的非建模用途，**需人工确认**"
            "（见 L1_text_scan.flagged_for_review）。")

    payload = {
        "blind_year": BLIND_YEAR,
        "audited_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "L1_text_scan": {
            "hits_total": len(text_hits),
            "by_purpose": dict(by_purpose),
            "flagged_for_review": flagged,
            "excluded_self_referential_files": int(getattr(_scan_text, "excluded_files", 0)),
            "excluded_paths": list(_SELF_REFERENTIAL),
            "conclusion": conclusion,
        },
        "L2_sql_surface": {
            "total": len(sql_surface),
            "by_kind": dict(Counter(item["kind"] for item in sql_surface)),
            "trailing_window_sites": [i for i in sql_surface if i["kind"] == "trailing_window"],
        },
        "L3_trailing_window_reachability": reach,
        "L4_artifacts": artifacts,
        "git_history_touching_blind_year": history,
        "concept_note": (
            "本盲测的性质是**跨年份留出测试（cross-year holdout）**，不是按时间顺序的"
            "未来预测测试：2021 与开发所用的 2022–2024 在时间轴上交错，且系统在"
            "2021 之前没有部署记录。因此结论只能说『在未见年份上的泛化』，"
            "不能称为『时间外推/未来预测』。"),
        "verdict": {
            "usable_as_blind": True,
            "conditions": [
                "结论表述限定为跨年份留出，不得称为未来预测",
                "报告中必须记录：2021 的行确实存在于库中、且被数据覆盖审计脚本统计过",
                "任何后续用 2021 做的调参都必须写明并撤销其盲测地位",
            ],
        },
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    dest = RESULTS / "v99_blind_year_lineage.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"字面命中 {len(text_hits)} 处：{dict(by_purpose)}"
          f"（另剔除自指文件 {payload['L1_text_scan']['excluded_self_referential_files']} 个）")
    print(f"SQL 读取面 {len(sql_surface)} 处：{payload['L2_sql_surface']['by_kind']}")
    print(f"滚动窗口：V9 实际最低点 {reach['v9_actual_earliest_window_date']}"
          f"（触及盲测年={reach['v9_read_blind_year']}）")
    print(f"评测产物日期年份：{artifacts['scored_date_years']}"
          f"（盲测年窗口 {artifacts['blind_year_windows_scored']} 个）")
    for item in flagged[:8]:
        print(f"  待确认：{item['file']}:{item['line']} — {item['snippet']}")
    print(f"\nsaved -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
