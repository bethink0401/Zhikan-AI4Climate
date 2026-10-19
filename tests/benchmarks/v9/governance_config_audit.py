"""V9.7：判定配置审计——把每套配置在**同一批输入**上逐例复算。

为什么需要：V9.6 报告里出现过"同一个 R1 却有两个数（CWB 0.206 / 0.460）"、
以及"CI 的基线到底是哪套配置"两类矛盾。根因都是**跨配置比较时没有把配置写清楚**。
本脚本把每套配置的来源（git 提交 / 当前文件 / 扫描网格）与全部指标一起输出，
并要求 CI 段落同时记录两侧配置与两侧点估计。

配置来源（逐字取自版本库，可复核）：
  A_v95_committed  : git 0a3e681:config/pollution_rules.json（产生 V9.5 留出结论的那套）
  B_v96_current    : 当前 config/pollution_rules.json（V9.6 合成的开发集标定结果）
  C_sweep_base     : V9.6 扫描脚本里被称作 "R1" 的那套（与 A 只差 min_strength）
  D_contrast_only  : C 的阈值 + 绝对对比度区域判据（V9.6 曾启用后回退的那套）
  E_synth_calib    : 合成开发集最优（= governance_sweep.py 的 frozen_config，逐键读取）
  F_calib_R1       : E 的阈值与局地项，但区域判据换成同步（E 的 CI 基线）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
for path in (str(BASE), str(HERE), str(HERE.parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

import governance_sweep as gs  # noqa: E402
from contracts import require_stats_consistent  # noqa: E402
from metrics import clustered_macro_f1_diff, macro_f1  # noqa: E402

# 共用底座：异常分门控 0.45、交通门控只认 NOx 年内分位（V9.5 起即为该形态）
_COMMON = {"gate_anomaly_min": 0.45, "traffic_gate_uses_no2_signal": False,
           "local_station_term": "none"}

# V9.9 修复：E/F 此前**硬编码** min_strength=0.35，而 governance_sweep.py 的实际
# 冻结配置（dev 网格最优）是 0.30——于是报告里出现两个都叫"合成标定"的数
# （扫描块 0.1848 / 审计表 0.2023）。现在 E 直接**从扫描 JSON 读取**，F 由 E 派生，
# 并加硬断言：审计里的 E 必须逐键等于扫描冻结配置，否则立刻失败。
_SWEEP = json.loads((HERE / "results" / "v96_rule_sweep.json").read_text(encoding="utf-8"))
_E_FROZEN = _SWEEP["frozen_config"]
_C_BASE = {**_SWEEP["design"]["base_config"], "regional_variant": "R1_sync"}

CONFIGS: dict[str, dict] = {
    "A_v95_committed": {**_COMMON, "min_strength": 0.35, "margin": 0.12,
                        "traffic_nox_pct_min": 0.55, "regional_variant": "R1_sync"},
    "B_v96_current": {**_COMMON, "min_strength": 0.35, "margin": 0.05,
                      "traffic_nox_pct_min": 0.65, "regional_variant": "R1_sync"},
    "C_sweep_base": dict(_C_BASE),
    "D_contrast_only": {**_C_BASE, "regional_variant": "R2_abs_contrast"},
    "E_synth_calib": dict(_E_FROZEN),
    "F_calib_R1": {**_E_FROZEN, "regional_variant": "R1_sync"},
}

if CONFIGS["E_synth_calib"] != _E_FROZEN:  # pragma: no cover - 防御性断言
    raise AssertionError("E_synth_calib 与 v96_rule_sweep.json 的 frozen_config 不一致")

PROVENANCE = {
    "A_v95_committed": "git 0a3e681:config/pollution_rules.json（V9.5 留出结论所用）",
    "B_v96_current": "当前 config/pollution_rules.json（V9.6 合成开发集标定）",
    "C_sweep_base": "V9.6 governance_sweep.py 的 base_config + R1_sync（逐键取自扫描 JSON）",
    "D_contrast_only": "C 的阈值 + R2_abs_contrast（V9.6 启用后回退）",
    "E_synth_calib": "V9.6 governance_sweep.py 的 frozen_config（dev 网格最优，逐键取自扫描 JSON）",
    "F_calib_R1": "E 的阈值/局地项 + R1_sync（E 的 CI 基线）",
}

STATIONS = ("Mong Kok Roadside", "Central Roadside", "Causeway Bay Roadside")


def _per_station(rows: list[dict], cfg: dict) -> dict[str, float]:
    out = {}
    for station in STATIONS:
        sub = [r for r in rows if r["station"] == station]
        out[station] = round(macro_f1([r["label"] for r in sub],
                                      [gs.predict(r, cfg) for r in sub]), 4)
    return out


def _regional_detail(rows: list[dict], cfg: dict) -> dict[str, dict]:
    """真值区域窗在每站的去向——用于回答"CWB 退化到底是谁造成的"。"""
    out = {}
    for station in STATIONS:
        sub = [r for r in rows if r["station"] == station
               and r["label"] == "regional_transport"]
        preds = [gs.predict(r, cfg) for r in sub]
        out[station] = {
            "n": len(sub),
            "predicted_regional": sum(1 for p in preds if p == "regional_transport"),
            "predicted_traffic": sum(1 for p in preds if p == "traffic_dominant"),
            "predicted_local": sum(1 for p in preds if p == "local_accumulation"),
            "abstained": sum(1 for p in preds if p == "uncertain"),
        }
    return out


def main() -> int:
    real = {mode: gs.load_real(mode) for mode in ("full", "pit")}
    print(f"真实窗 {len(real['full'])}（full）/ {len(real['pit'])}（pit）", flush=True)

    payload: dict = {"provenance": PROVENANCE, "configs": CONFIGS, "modes": {}}
    for mode, rows in real.items():
        truths = [r["label"] for r in rows]
        entry = {"metrics": {}, "comparisons": {}}
        print(f"\n=== {mode} 口径 ===")
        print(f"{'配置':<18} {'F1':>7} {'弃判':>7} " +
              " ".join(f"{s.split()[0]:>10}" for s in STATIONS) +
              f" {'区域预测数':>10} {'区域召回':>8}")
        for name, cfg in CONFIGS.items():
            preds = [gs.predict(r, cfg) for r in rows]
            regional_pool = [t for t in truths if t == "regional_transport"]
            regional_hit = sum(1 for t, p in zip(truths, preds)
                               if t == "regional_transport" and p == "regional_transport")
            metrics = {
                "macro_f1": round(macro_f1(truths, preds), 4),
                "abstention_rate": round(sum(1 for p in preds if p == "uncertain")
                                         / len(preds), 4),
                "per_station_f1": _per_station(rows, cfg),
                "regional_predicted": preds.count("regional_transport"),
                "regional_recall": round(regional_hit / len(regional_pool), 4),
                "regional_detail": _regional_detail(rows, cfg),
                "config": cfg,
            }
            entry["metrics"][name] = metrics
            print(f"{name:<18} {metrics['macro_f1']:>7.4f} "
                  f"{metrics['abstention_rate']:>7.3f} " +
                  " ".join(f"{metrics['per_station_f1'][s]:>10.4f}" for s in STATIONS) +
                  f" {metrics['regional_predicted']:>10} {metrics['regional_recall']:>8.3f}")

        # 显式命名的配对比较：两侧配置、两侧点估计、配对单位全部落盘
        pairs = [("E_synth_calib", "A_v95_committed"),
                 ("E_synth_calib", "F_calib_R1"),
                 ("D_contrast_only", "C_sweep_base"),
                 ("A_v95_committed", "C_sweep_base"),
                 ("B_v96_current", "A_v95_committed")]
        print(f"\n  --- {mode} 配对比较（按日期聚类 95% CI）---")
        for arm, baseline in pairs:
            arm_preds = [gs.predict(r, CONFIGS[arm]) for r in rows]
            base_preds = [gs.predict(r, CONFIGS[baseline]) for r in rows]
            ci = clustered_macro_f1_diff(truths, arm_preds, base_preds,
                                         [r["date"] for r in rows], reps=10000)
            require_stats_consistent(
                macro_f1(truths, arm_preds) - macro_f1(truths, base_preds),
                ci["mean_diff"], context=f"{mode} {arm}−{baseline}")
            record = {
                **ci,
                "arm": arm, "baseline": baseline,
                "arm_config": CONFIGS[arm], "baseline_config": CONFIGS[baseline],
                "arm_point_f1": round(macro_f1(truths, arm_preds), 4),
                "baseline_point_f1": round(macro_f1(truths, base_preds), 4),
                "paired_unit": "date", "n": len(rows),
                "n_clusters": len({r["date"] for r in rows}),
            }
            entry["comparisons"][f"{arm}_minus_{baseline}"] = record
            print(f"  {arm:<18} − {baseline:<18} = {ci['mean_diff']:+.4f} "
                  f"CI [{ci['ci95'][0]:+.4f}, {ci['ci95'][1]:+.4f}] "
                  f"({record['baseline_point_f1']} → {record['arm_point_f1']})")
        payload["modes"][mode] = entry

    dest = HERE / "results" / "v97_config_audit.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved -> {dest}")

    # 在线/离线一致性：若在线留出刚复跑过，比对同配置的逐例结果
    online = BASE / "tests" / "benchmarks" / "results" / "v9_governance.json"
    online_cases = HERE / "results" / "v9_governance_cases.csv"
    if online.exists() and online_cases.exists():
        import csv
        recorded = {f"{r['station']}|{r['date']}|{r['start_hour']}": r["C"]
                    for r in csv.DictReader(online_cases.open(encoding="utf-8"))}
        rows = real["full"]
        offline = [gs.predict(r, CONFIGS["B_v96_current"]) for r in rows]
        keys = [f"{r['station']}|{r['date']}|{r['start_hour']}" for r in rows]
        matched = sum(1 for k, p in zip(keys, offline)
                      if recorded.get(k) == p)
        payload["online_offline_check"] = {
            "online_file": str(online),
            "config": CONFIGS["B_v96_current"],
            "offline_config": "B_v96_current",
            "compared": len(keys), "matched": matched,
            "verdict": "PASS" if matched == len(keys) else "FAIL",
            "note": "在线文件必须是用同一配置跑出的（config 记录见 v9_governance.json）",
        }
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"在线/离线逐例一致：{matched}/{len(keys)} "
              f"（{'PASS' if matched == len(keys) else 'FAIL'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
