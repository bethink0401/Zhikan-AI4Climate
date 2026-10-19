"""V9.8：正式运行清单（Git SHA + 配置哈希 + 数据哈希 + 冻结配置）。

审查要求（原文）："固定创新一、创新三正式运行配置，记录完整 Git SHA、配置哈希、
数据哈希。"本脚本把这三类指纹一次算齐，写进 `results/v98_run_manifest.json`，
此后任何"这批结果对应哪份代码/配置/数据"的问题都能一句话回答。

用法：
    /usr/bin/python3 tests/benchmarks/v9/run_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
RESULTS = HERE / "results"

CONFIG_FILES = ("config/pollution_rules.json", "config/travel_profile.json",
                "config/meteorology_rules.json")
DATA_FILES = ("data/air_quality.db", "data/traffic.db")

#: V9.9：冻结范围从"配置 + 数据"扩到"代码 + 测试协议 + 气象缓存"（审查第 2 步）。
#: 只有把产生结论的**全部输入**都钉住，哈希清单才能回答"这批结果对应哪一份东西"。
CODE_FILES = (
    "tests/benchmarks/v9/protocol.json",
    "tests/benchmarks/v9/metrics.py",
    "tests/benchmarks/v9/contracts.py",
    "tests/benchmarks/v9/governance_capture.py",
    "tests/benchmarks/v9/governance_sweep.py",
    "tests/benchmarks/v9/governance_config_audit.py",
    "tests/benchmarks/v9/governance_holdout.py",
    "tests/benchmarks/v9/governance_pit.py",
    "tests/benchmarks/v9/meteo_cache.py",
    "tests/benchmarks/v9/make_report_tables.py",
    "tests/benchmarks/v9/audit_report_claims.py",
    "tests/benchmarks/v9/audit_test_inventory.py",
    "tests/benchmarks/v9/audit_blind_year_lineage.py",
    "agents/source_agent.py",
    "agents/air_quality_agent.py",
    "tools/air_quality_tools.py",
    "tools/travel_advice_tools.py",
)
#: 气象缓存目录：收录**逐月文件的清单哈希**（内容哈希 + 文件名，排序后汇总），
#: 这样"缓存被换过"一定改变清单，但不必把每个 CSV 的大小都写进报告。
METEO_CACHE_DIR = "tests/benchmarks/results/meteo_cache"


def _dir_digest(path: Path) -> dict:
    """对目录做确定性汇总哈希：按文件名排序后拼接 (name, sha256) 再取 SHA-256。"""
    if not path.is_dir():
        return {}
    entries = sorted(p for p in path.rglob("*") if p.is_file())
    digests = [(str(p.relative_to(path)), _sha256(p)) for p in entries]
    rollup = hashlib.sha256(
        "\n".join(f"{name}:{digest}" for name, digest in digests).encode("utf-8")
    ).hexdigest()
    return {"file_count": len(digests), "rollup_sha256": rollup,
            "sample": dict(digests[:3])}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=BASE, capture_output=True,
                          text=True).stdout.strip()


def main() -> int:
    from agents.source_agent import load_source_rules
    from tools.travel_advice_tools import decision_config, load_travel_config

    rules = load_source_rules()
    travel = load_travel_config()
    pruned = {k: v for k, v in rules.items() if not str(k).startswith("_")}

    manifest = {
        "manifest_version": "V9.9",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        # 注意：SHA 指向**写入本清单时的 HEAD**；清单自身随后被提交，因此若在
        # 提交后立刻重算，sha 会比清单文件所在提交早一代。dirty 反映生成时刻
        # 工作区是否有未提交改动。
        "git": {
            "sha": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "config_hashes": {name: _sha256(BASE / name) for name in CONFIG_FILES
                          if (BASE / name).exists()},
        "data_hashes": {name: _sha256(BASE / name) for name in DATA_FILES
                        if (BASE / name).exists()},
        "code_hashes": {name: _sha256(BASE / name) for name in CODE_FILES
                        if (BASE / name).exists()},
        "meteo_cache": _dir_digest(BASE / METEO_CACHE_DIR),
        "frozen_configs": {
            # 创新一：现网判定规则（V9.6 合成开发集标定，V9.7 逐例复算确认）
            "innovation_1_judgement": {
                "judgement_rule": pruned.get("judgement_rule"),
                "min_strength": pruned.get("min_strength"),
                "dominance_margin": pruned.get("dominance_margin"),
                "traffic_nox_pct_min": pruned.get("traffic_nox_pct_min"),
                "gate_anomaly_min": pruned.get("gate_anomaly_min"),
                "signature_weights": pruned.get("signature_weights"),
                "effective_config_sha256": hashlib.sha256(
                    json.dumps(pruned, sort_keys=True, ensure_ascii=False)
                    .encode("utf-8")).hexdigest(),
            },
            # 创新三：决策规则（Pareto 筛选 + Tchebycheff 选择；权重方案见 weight_basis）
            "innovation_3_decision": decision_config(travel),
            # 创新二：冻结（本轮零改动），参数见 config/pollution_rules.json 的 field/calm 段
            "innovation_2_note": "frozen since V9.4/V9.5; no model change in V9.6-V9.8",
        },
        "blind_data_status": {
            "used_for_development": [2022, 2023, 2024],
            "never_used_by_modeling_threshold_scoring": [2018, 2019, 2020, 2021],
            "caveat_on_v98_wording": "V9.8 曾写『从未被任何基准或分析脚本引用』——不准确："
                                     "examples/audit_day1_9.py 会逐年份统计（含 2021）数据表行数；"
                                     "正确表述是『从未被任何建模/阈值/评分脚本使用』。"
                                     "谱系审计见 results/v99_blind_year_lineage.json。",
            "preregistered_blind_year": 2021,
            "reserve_years": [2018, 2019, 2020],
            "blind_year_status": "EXECUTED_ONCE（2026-10-14）——自此 regression_only，"
                                 "不得再称盲测、不得用于调参；结果见 "
                                 "tests/benchmarks/results/v9_governance_blind2021.json",
            "caveat": "2020 为疫情年，污染物水平异常，列备选；2021 为主盲测年。"
                      "盲测年一旦开跑即写入 regression_only，不得再称盲测。"
                      "性质为跨年份留出（cross-year holdout），不是按时间顺序的未来预测测试。",
        },
        "lineage_audit": "tests/benchmarks/v9/audit_blind_year_lineage.py",
        "claim_audit": "tests/benchmarks/v9/audit_report_claims.py",
        "test_inventory": "tests/benchmarks/v9/audit_test_inventory.py",
    }
    dest = RESULTS / "v98_run_manifest.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("git", "data_hashes")},
                     ensure_ascii=False, indent=2))
    print(f"\nsaved -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
