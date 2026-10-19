# -*- coding: utf-8 -*-
"""V9.5 评测器口径一致性回归测试。

背景：V9.1 与 V9.5 两次发现同一个错误——报告表格里的指标是 Macro-F1，
而置信区间却用"逐窗命中率差"（0/1 均值差）计算，两者不是同一个统计量，
导致同一份数据给出互相矛盾的数字。

本测试锁定三件事：
  1. clustered_macro_f1_diff 的 mean_diff 必须精确等于 Macro-F1 点估计差；
  2. 它确实与 clustered_paired_diff（命中率口径）不同——差异不是实现的
     偶然，而是统计量本身不同；
  3. 两套口径在置信区间的"是否含 0"结论上可以不同（这正是必须统一口径的
     原因）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "benchmarks" / "v9"))

from metrics import (clustered_macro_f1_diff, clustered_paired_diff,  # noqa: E402
                     macro_f1)


def _toy_cases() -> tuple[list[str], list[str], list[str], list[str]]:
    """构造一个 4 类、类别不均衡的小样本，使两种口径必然不同。"""
    truths = (["traffic"] * 6 + ["regional"] * 6 + ["local"] * 4 + ["uncertain"] * 4)
    # 方法 X：每类各错 1~2 个
    preds_x = (["traffic"] * 5 + ["local"] + ["regional"] * 4 + ["traffic", "local"]
               + ["local"] * 3 + ["traffic"] + ["uncertain"] * 3 + ["local"])
    # 方法 Y：整体更差
    preds_y = (["traffic"] * 4 + ["uncertain"] * 2 + ["regional"] * 3 + ["uncertain"] * 3
               + ["traffic"] * 4 + ["uncertain"] * 4)
    clusters = ([f"d{i // 2}" for i in range(len(truths))])
    return truths, preds_x, preds_y, clusters


def test_macro_f1_diff_matches_point_estimate() -> None:
    truths, px, py, clusters = _toy_cases()
    result = clustered_macro_f1_diff(truths, px, py, clusters, reps=500)
    expected = round(macro_f1(truths, px) - macro_f1(truths, py), 6)
    assert result["mean_diff"] == round(expected, 4), (
        f"CI 的 mean_diff 必须等于 Macro-F1 点估计差："
        f"{result['mean_diff']} vs {expected}")


def test_macro_f1_diff_differs_from_hit_rate() -> None:
    """两种口径在类别不均衡时给出不同答案——证明它们不是同一个统计量。"""
    truths, px, py, clusters = _toy_cases()
    f1_ci = clustered_macro_f1_diff(truths, px, py, clusters, reps=2000)
    hit_ci = clustered_paired_diff(
        [1.0 if a == t else 0.0 for a, t in zip(px, truths)],
        [1.0 if b == t else 0.0 for b, t in zip(py, truths)],
        clusters, reps=2000)
    assert f1_ci["mean_diff"] != hit_ci["mean_diff"], (
        "Macro-F1 差与逐窗命中率差在小样本/不均衡下必然不同——"
        "若相等说明测试数据退化，无法起到守护作用")
    # 命中率口径只看"判对没判对"，Macro-F1 还看"错成了哪一类"：
    # 这里 X 的命中率更高（错得少），但错时错得更远，Macro-F1 未必更好
    assert abs(f1_ci["mean_diff"] - hit_ci["mean_diff"]) > 1e-3


def test_macro_f1_only_uses_union_labels() -> None:
    """Macro-F1 的类别集合 = 真值∪预测，避免"未预测到的类"被漏算。"""
    assert macro_f1(["a", "a", "b"], ["a", "a", "a"]) < 1.0
    assert macro_f1(["a", "a", "a"], ["a", "a", "a"]) == 1.0


def test_clustered_macro_f1_diff_requires_aligned_lengths() -> None:
    try:
        clustered_macro_f1_diff(["a"], ["a", "b"], ["a"], ["d"], reps=10)
    except ValueError:
        return
    raise AssertionError("长度不一致时应抛 ValueError")
