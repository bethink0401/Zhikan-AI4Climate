#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成三项对比实验的 PPT 图表（PNG）与汇总 CSV。

读取 tests/benchmarks/results/ 下的三个结果 JSON：
  governance_baselines.json / propagation_baselines.json / travel_multigoal.json
输出到 tests/benchmarks/results/figures/：
  exp1_*  实验一（研判质量 / 耗时 / 证据追溯 / 弱证据误判）
  exp2_*  实验二（分类别 IoU / 面积预算 P-R / 风速连续性）
  exp3_*  实验三（时间-暴露散点 / 方法级指标对比柱状）
  summary_exp{1,2,3}.csv  三张方法级对比表

用法：/opt/anaconda3/bin/python3 tests/benchmarks/make_experiment_figures.py
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent / "results"
FIGURES = RESULTS / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

# 中文显示（macOS 自带字体）
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

#: 统一配色：A 传统方法灰 / B 基线蓝 / C 智瞰红（涨红原则呼应主题色）
COLORS = {"A": "#8a8f98", "B": "#3b74d0", "C": "#d03a3b", "Cw": "#e08b3a"}
LABELS = {
    "A": "A 传统方法", "B": "B 基线", "C": "C 智瞰", "Cw": "C 智瞰(污染加权)",
}


def load(name: str) -> dict:
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)


def save(fig, name: str) -> Path:
    path = FIGURES / name
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  figure -> {path.name}")
    return path


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  csv    -> {path.name}")


# ── 实验一 ──────────────────────────────────────────────────────────────

def figures_exp1() -> None:
    data = load("governance_baselines")
    summary = data["summary_test"]["methods"]
    weak_summary = summary  # 周末负向窗口已并入测试集，弱证据指标就在其中
    methods = ["A", "B", "C"]
    names = [LABELS[m] for m in methods]

    macro = [summary[m]["macro_f1"] for m in methods]
    trace = [summary[m]["mean_traceability"] for m in methods]
    complete = [summary[m]["completion_rate"] for m in methods]
    times = [summary[m]["median_ms"] for m in methods]
    weak = [weak_summary[m]["weak_evidence_pred_traffic"] for m in methods]
    weak_flagged = [weak_summary[m]["weak_evidence_flagged"] for m in methods]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    panels = [
        (axes[0], macro, "来源判断 Macro-F1（越高越好）", "%.2f"),
        (axes[1], trace, "有效证据追溯率（0-1）", "%.2f"),
        (axes[2], complete, "完整任务成功率", "%.2f"),
    ]
    for ax, values, title, fmt in panels:
        bars = ax.bar(names, values, color=[COLORS[m] for m in methods], width=0.55)
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, 1.05)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.02,
                    fmt % value, ha="center", fontsize=9)
        style_axes(ax)
    save(fig, "exp1_quality.png")

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    bars = axes[0].bar(names, times, color=[COLORS[m] for m in methods], width=0.55)
    axes[0].set_yscale("log")
    axes[0].set_title("单窗口研判耗时中位数（ms，对数轴，越低越好）", fontsize=11)
    for bar, value in zip(bars, times):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value * 1.15,
                     f"{value:,.0f}" if value >= 1 else f"{value:.2f}",
                     ha="center", fontsize=9)
    style_axes(axes[0])

    x = range(len(methods))
    width = 0.35
    weak_vals = [int(w.split("/")[0]) / max(int(w.split("/")[1]), 1) for w in weak]
    flag_vals = [int(w.split("/")[0]) / max(int(w.split("/")[1]), 1) for w in weak_flagged]
    axes[1].bar([i - width / 2 for i in x], weak_vals, width, label="仍判定交通源比例",
                color=COLORS["A"])
    axes[1].bar([i + width / 2 for i in x], flag_vals, width, label="给出缺失标记比例",
                color=COLORS["C"])
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(names)
    axes[1].set_ylim(0, 1.05)
    axes[1].set_title("弱证据负向窗口（周末，无 ATC 数据）", fontsize=11)
    axes[1].legend(fontsize=8)
    for i, (w, f) in enumerate(zip(weak, weak_flagged)):
        axes[1].text(i - width / 2, weak_vals[i] + 0.02, w, ha="center", fontsize=8)
        axes[1].text(i + width / 2, flag_vals[i] + 0.02, f, ha="center", fontsize=8)
    style_axes(axes[1])
    save(fig, "exp1_time_weak.png")

    write_csv(RESULTS / "summary_exp1.csv",
              ["method", "macro_f1", "median_ms", "traceability", "completion",
               "weak_evidence_pred_traffic", "weak_evidence_flagged"],
              [[LABELS[m], summary[m]["macro_f1"], summary[m]["median_ms"],
                summary[m]["mean_traceability"], summary[m]["completion_rate"],
                summary[m]["weak_evidence_pred_traffic"],
                summary[m]["weak_evidence_flagged"]] for m in methods])


# ── 实验二 ──────────────────────────────────────────────────────────────

def figures_exp2() -> None:
    data = load("propagation_baselines")
    test = data["test_summary"]["methods"]
    methods = ["A", "B", "C"]
    categories = ["stable", "veering", "cross_zero", "calm", "gust"]
    cat_labels = ["稳定风", "转向", "跨零点", "静风", "阵风"]

    fig, axes = plt.subplots(1, 3, figsize=(14, 3.8))
    # 1) 分类别 IoU
    width = 0.26
    x = range(len(categories))
    for k, method in enumerate(methods):
        values = [test[method]["by_category"][c]["mean_iou"] for c in categories]
        axes[0].bar([i + (k - 1) * width for i in x], values, width,
                    label=LABELS[method], color=COLORS[method])
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(cat_labels)
    axes[0].set_title("风险区域 IoU（独立测试集，分类别）", fontsize=11)
    axes[0].legend(fontsize=8)
    style_axes(axes[0])

    # 2) 同面积预算下的 POI P-R
    for method in methods:
        precision = test[method]["budget_precision"]["mean"]
        recall = test[method]["budget_recall"]["mean"]
        axes[1].scatter(recall, precision, s=110, color=COLORS[method],
                        label=LABELS[method], zorder=3)
        axes[1].annotate(method, (recall, precision), textcoords="offset points",
                         xytext=(8, 4), fontsize=9)
    axes[1].set_xlabel("敏感目标 Recall（同面积预算）")
    axes[1].set_ylabel("敏感目标 Precision（同面积预算）")
    axes[1].set_title("敏感目标识别（相同预警面积预算）", fontsize=11)
    axes[1].set_xlim(0, 1.05)
    axes[1].set_ylim(0, 1.05)
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25, linewidth=0.6)

    # 3) 风速连续性：风险区域面积随风速变化
    sweep = data["continuity"]["speed_sweep_wind_from_270"]
    speeds = data["continuity"]["speed_values_ms"]
    for method in methods:
        axes[2].plot(speeds, sweep[method]["area_km2_series"],
                     color=COLORS[method], label=LABELS[method], linewidth=1.8)
    axes[2].axvspan(0.5, 1.2, color="#f2c94c", alpha=0.18)
    axes[2].text(0.85, axes[2].get_ylim()[1] * 0.92, "静风过渡区", fontsize=8, ha="center")
    axes[2].set_xlabel("风速 (m/s)")
    axes[2].set_ylabel("风险区域面积 (km²)")
    axes[2].set_title("风速细步长扫描下的风险区域连续性", fontsize=11)
    axes[2].legend(fontsize=8)
    style_axes(axes[2])
    save(fig, "exp2_propagation.png")

    write_csv(RESULTS / "summary_exp2.csv",
              ["method", "mean_iou", "area_km2", "poi_precision", "poi_recall",
               "budget_precision", "budget_recall", "field_auc", "median_runtime_ms"],
              [[LABELS[m], test[m]["mean_iou"]["mean"], test[m]["mean_area_km2"]["mean"],
                test[m]["mean_poi_precision"]["mean"], test[m]["mean_poi_recall"]["mean"],
                test[m]["budget_precision"]["mean"], test[m]["budget_recall"]["mean"],
                test[m]["mean_field_auc"]["mean"], test[m]["median_runtime_ms"]]
               for m in methods])


# ── 实验三 ──────────────────────────────────────────────────────────────

def figures_exp3() -> None:
    data = load("travel_v4")
    test = data["blind_summary"]["methods"]
    blind_date = data["design"]["dates"]["blind_independent"]
    scatter = []
    for row in data["rows"]:
        if row.get("date") != blind_date:
            continue
        for name in ("A_shortest_time", "B_lowest_intake", "C_multigoal"):
            entry = (row.get("methods") or {}).get(name) or {}
            if "duration_minutes" in entry:
                scatter.append({
                    "method": name, "case": row["case"], "profile": row["profile"],
                    "slot": row["slot"],
                    "duration_minutes": entry["duration_minutes"],
                    "intake_index": entry["intake_index"],
                })
    order = [("A_shortest_time", "A"), ("B_lowest_intake", "B"),
             ("C_multigoal", "C")]
    names = [LABELS[key] for _, key in order]

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.2))
    # 1) 时间-暴露散点
    shown = set()
    for point in scatter:
        key = next(v for k, v in order if k == point["method"])
        label = LABELS[key] if key not in shown else None
        shown.add(key)
        axes[0].scatter(point["duration_minutes"], point["intake_index"],
                        s=26, alpha=0.65, color=COLORS[key], label=label)
    axes[0].set_xlabel("出行时间（分钟）")
    axes[0].set_ylabel("相对暴露 EU")
    axes[0].set_title("独立测试集：推荐方案的时间—暴露分布", fontsize=11)
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.25, linewidth=0.6)

    # 2) 方法级指标
    metrics = [
        ("平均出行时间 (min)", [test[k]["mean_duration_min"] for k, _ in order], None),
        ("平均暴露 EU", [test[k]["mean_intake_EU"] for k, _ in order], None),
        ("偏好满足率", [test[k]["pref_satisfaction_rate"] for k, _ in order], (0, 1)),
        ("非支配率", [test[k]["nondominated_rate"] for k, _ in order], (0, 1)),
    ]
    width = 0.2
    x = range(len(metrics))
    for k, (_, key) in enumerate(order):
        values = [m[1][k] for m in metrics]
        axes[1].bar([i + (k - 1.5) * width for i in x], values, width,
                    label=LABELS[key], color=COLORS[key])
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels([m[0] for m in metrics], fontsize=9)
    axes[1].set_title("独立测试集方法级指标", fontsize=11)
    axes[1].legend(fontsize=8)
    style_axes(axes[1])
    save(fig, "exp3_travel.png")

    write_csv(RESULTS / "summary_exp3.csv",
              ["method", "mean_duration_min", "mean_intake_EU", "pref_satisfaction",
               "nondominated", "median_d_time_vs_A_pct", "median_d_intake_vs_A_pct"],
              [[LABELS[key], test[k]["mean_duration_min"], test[k]["mean_intake_EU"],
                test[k]["pref_satisfaction_rate"], test[k]["nondominated_rate"],
                test[k]["median_d_time_vs_A_pct"], test[k]["median_d_intake_vs_A_pct"]]
               for k, key in order])


def figures_exp3_constrained() -> None:
    """V5 公平指标：相同 EU 上限下的可行率与偏好满足（约束条件对比）。"""
    data = load("v5_analysis")
    constrained = data["travel_constrained"]
    methods = [("A_shortest_time", "A"), ("B_lowest_intake", "B"), ("C_multigoal", "C")]
    names = [LABELS[key] for _, key in methods]
    caps = list(constrained["eu_cap_comparison"]["A_shortest_time"].keys())

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.0))
    x = range(len(caps))
    width = 0.25
    for k, (full, key) in enumerate(methods):
        values = [constrained["eu_cap_comparison"][full][cap]["feasible_rate"] for cap in caps]
        axes[0].bar([i + (k - 1) * width for i in x], values, width,
                    label=names[k], color=COLORS[key])
        for i, v in enumerate(values):
            axes[0].text(i + (k - 1) * width, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels([c.split("≤ ")[1] for c in caps], fontsize=9)
    axes[0].set_ylim(0, 1.12)
    axes[0].set_ylabel("约束满足率（推荐可行比例）")
    axes[0].set_title("相同污染暴露上限下的推荐可行性", fontsize=11)
    axes[0].legend(fontsize=8)
    style_axes(axes[0])

    budgets = list(constrained["time_budget_comparison"]["A_shortest_time"].keys())
    for k, (full, key) in enumerate(methods):
        values = [constrained["time_budget_comparison"][full][b].get("pref_satisfaction_feasible")
                  for b in budgets]
        axes[1].bar([i + (k - 1) * width for i in x], values, width,
                    label=names[k], color=COLORS[key])
        for i, v in enumerate(values):
            if v is not None:
                axes[1].text(i + (k - 1) * width, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels([b.split("≤ ")[1] for b in budgets], fontsize=9)
    axes[1].set_ylim(0, 1.12)
    axes[1].set_ylabel("偏好满足率（可行推荐内）")
    axes[1].set_title("相同时间预算下的用户偏好满足", fontsize=11)
    axes[1].legend(fontsize=8)
    style_axes(axes[1])
    save(fig, "exp3_constrained.png")


def figures_exp3_v6() -> None:
    """V6 同约束对比：三个指标面板（中位耗时 / 平均EU / 偏好满足率）。"""
    data = load("travel_v6")
    summary = data["summary"]
    methods = [("A_constrained", "A"), ("B_constrained", "B"), ("C_system", "C")]
    names = [LABELS[key] for _, key in methods]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    metrics = [
        ("中位耗时（分钟）", [summary[full]["median_time_min"] for full, _ in methods], "%.1f"),
        ("平均暴露 EU", [summary[full]["mean_eu"] for full, _ in methods], "%.3f"),
        ("偏好满足率", [summary[full]["pref_satisfaction"] for full, _ in methods], "%.3f"),
    ]
    for ax, (title, values, fmt), best_low in zip(
            axes, metrics, (True, True, False)):
        bars = ax.bar(names, values, color=[COLORS[key] for _, key in methods], width=0.55)
        ax.set_title(title, fontsize=11)
        top = max(values)
        ax.set_ylim(0, top * 1.18)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + top * 0.03,
                    fmt % value, ha="center", fontsize=9)
        style_axes(ax)
    fig.suptitle("V6 同约束对比（暴露上限 = 候选最优 EU×1.25，盲测日 2023-10-25，全部 60/60 严格可行）",
                 fontsize=11, y=1.02)
    save(fig, "exp3_v6_constrained.png")


def figures_v8() -> None:
    """V8：2×2 消融 + 拉格朗日外部验证。"""
    # ── 2×2 消融 ──
    data = load("travel_v8")
    summary = data["summary"]
    arms = [("A_base_cand_base_dec", "A 基础候选\n基础决策"),
            ("B_base_cand_zhikan_dec", "B 基础候选\n智瞰决策"),
            ("C_full_cand_base_dec", "C 增强候选\n基础决策"),
            ("D_full_cand_zhikan_dec", "D 增强候选\n智瞰决策")]
    names = [label for _, label in arms]
    colors = [COLORS["A"], COLORS["B"], COLORS["C"], COLORS["B"]]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    metrics = [
        ("中位耗时（分钟）", "median_time_min", True),
        ("平均暴露 EU", "mean_eu", True),
        ("偏好满足率（双细则均值）",
         None, False),  # 手工画
    ]
    for ax, (title, key, lower_better) in zip(axes, metrics):
        if key is not None:
            values = [summary[full][key] for full, _ in arms]
        else:
            values = [statistics.mean([summary[full]["pref_satisfaction_v1"],
                                       summary[full]["pref_satisfaction_v2"]])
                      for full, _ in arms]
        bars = ax.bar(names, values, color=colors, width=0.6)
        ax.set_title(title, fontsize=10)
        top = max(values)
        ax.set_ylim(0, top * 1.2)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + top * 0.03,
                    f"{value:.3f}" if value < 2 else f"{value:.1f}",
                    ha="center", fontsize=9)
        ax.tick_params(axis="x", labelsize=8)
        style_axes(ax)
    fig.suptitle("V8 2×2 消融（盲测日 2023-11-29）：候选增强贡献为 0，差异全部来自决策器",
                 fontsize=11, y=1.02)
    save(fig, "exp3_v8_ablation.png")

    # ── 拉格朗日外部验证 ──
    lag = load("propagation_lagrangian")
    eul = load("propagation_v7")
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.0), sharey=True)
    methods = ["A", "B", "C"]
    for ax, (title, test_summary, diff) in zip(axes, [
            ("欧拉差分参考（V7）", eul["test_summary"]["methods"],
             eul["bootstrap_test"]["iou_diff_C_minus_B"]),
            ("拉格朗日粒子参考（V8 外部验证）", lag["test_summary"]["methods"],
             lag["iou_diff_test"]["C_minus_B"])]):
        overall = []
        for m in methods:
            value = test_summary[m]["mean_iou"]
            overall.append(value["mean"] if isinstance(value, dict) else value)
        bars = ax.bar(methods, overall, color=[COLORS[m] for m in methods], width=0.55)
        ax.set_title(title, fontsize=10)
        ax.set_ylim(0, 0.62)
        for bar, value in zip(bars, overall):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.015,
                    f"{value:.3f}", ha="center", fontsize=9)
        ci = diff["ci95"]
        ax.text(0.5, 0.94, f"C−B 差值 95% CI [{ci[0]:+.3f}, {ci[1]:+.3f}]",
                transform=ax.transAxes, ha="center", fontsize=9,
                bbox=dict(boxstyle="round", fc="#FFF7E6", ec="#CCCCCC"))
        style_axes(ax)
    axes[0].set_ylabel("总体 IoU（测试集）")
    fig.suptitle("V8 外部有效性：更换参考模型结构后优势保留", fontsize=11, y=1.0)
    save(fig, "exp2_lagrangian.png")


def main() -> int:
    print("生成 V8 图表...")
    figures_v8()
    print("生成实验一图表...")
    figures_exp1()
    print("生成实验二图表...")
    figures_exp2()
    print("生成实验三图表...")
    figures_exp3()
    figures_exp3_constrained()
    figures_exp3_v6()
    print(f"完成，输出目录: {FIGURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
