# -*- coding: utf-8 -*-
"""V9 图表：治理混淆矩阵 + 聚类 CI 森林图。用 anaconda python 运行。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti TC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"
COLORS = {"A": "#8C8C8C", "B": "#4C78A8", "Bplus": "#72B7B2", "C": "#E45756"}


def load(name):
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))


def fig_cm() -> None:
    data = load("v9_governance")
    cms = data["confusion"]
    methods = ["B", "C"]
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.4))
    for ax, method in zip(axes, methods):
        cm = cms[method]
        labels = list(cm.keys())
        grid = [[cm[t][p] for p in labels] for t in labels]
        im = ax.imshow(grid, cmap="Blues")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=8)
        for i in range(len(labels)):
            for j in range(len(labels)):
                ax.text(j, i, grid[i][j], ha="center", va="center", fontsize=9,
                        color="white" if grid[i][j] > max(map(max, grid)) / 2 else "black")
        ax.set_title(f"{'B 多源规则' if method == 'B' else 'C 智瞰'}（行=标注真值）",
                     fontsize=10)
    fig.suptitle("V9 留出年 2024 · 主标注混淆矩阵（254 窗 / 147 日期聚类）", fontsize=11)
    fig.tight_layout()
    fig.savefig(RESULTS / "v9_governance_cm.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_ci() -> None:
    data = load("v9_governance")
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.0))

    # 左：聚类 CI 森林图
    cis = data["clustered_ci_vs_B"]
    items = [("C", cis["C"]), ("Bplus", cis["Bplus"])]
    ax = axes[0]
    for i, (name, entry) in enumerate(items):
        lo, hi = entry["ci95"]
        ax.plot([lo, hi], [i, i], color=COLORS[name], lw=2)
        ax.plot(entry["mean_diff"], i, "o", color=COLORS[name])
        ax.text(hi + 0.006, i, f"+{entry['mean_diff']:.3f} [{lo:+.3f}, {hi:+.3f}]",
                va="center", fontsize=9)
    ax.axvline(0, color="#999999", ls="--", lw=1)
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels([n for n, _ in items])
    ax.set_xlabel("相对 B 的命中率差（按日期聚类 95% CI）")
    ax.set_title("聚类配对差值（n=254，147 日期簇）", fontsize=10)
    ax.set_ylim(-0.6, 1.6)

    # 右：分站点 Macro-F1
    by_station = data["by_station"]
    stations = list(by_station.keys())
    methods = ["A", "B", "Bplus", "C"]
    import numpy as np
    x = np.arange(len(stations))
    width = 0.2
    for k, method in enumerate(methods):
        values = [by_station[s][method] for s in stations]
        axes[1].bar(x + (k - 1.5) * width, values, width, label=method,
                    color=COLORS[method])
        for xi, v in zip(x + (k - 1.5) * width, values):
            axes[1].text(xi, v + 0.01, f"{v:.2f}", ha="center", fontsize=7)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([s.replace(" Roadside", "") for s in stations], fontsize=9)
    axes[1].set_ylim(0, 0.72)
    axes[1].set_ylabel("Macro-F1（主标注）")
    axes[1].legend(fontsize=8, ncol=4)
    axes[1].set_title("分站点一致性（2024 留出年）", fontsize=10)
    fig.suptitle("V9 实验一：未见年份泛化（2024，冻结系统零调参）", fontsize=11)
    fig.tight_layout()
    fig.savefig(RESULTS / "v9_governance_ci.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    fig_cm()
    fig_ci()
    print("figures -> v9_governance_cm.png, v9_governance_ci.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
