# -*- coding: utf-8 -*-
"""V9 共享统计工具：聚类 bootstrap、覆盖率—风险曲线、混淆矩阵。"""
from __future__ import annotations

import random
import statistics
from collections import Counter
from typing import Callable, Iterable


def macro_f1(truths: list[str], preds: list[str]) -> float:
    """全类别（含 uncertain）等权 Macro-F1。"""
    labels = sorted(set(truths) | set(preds))
    f1s = []
    for lab in labels:
        tp = sum(1 for t, p in zip(truths, preds) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(truths, preds) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(truths, preds) if t == lab and p != lab)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall)
                   if precision + recall else 0.0)
    return statistics.mean(f1s) if f1s else 0.0


def confusion(truths: list[str], preds: list[str]) -> dict:
    labels = sorted(set(truths) | set(preds))
    matrix = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(truths, preds):
        matrix[t][p] += 1
    return matrix


def clustered_paired_diff(x: list[float], y: list[float],
                          clusters: list[str],
                          reps: int = 10000, seed: int = 42) -> dict:
    """配对差值的聚类 bootstrap：按聚类单位重采样（如日期/OD）。

    同一聚类内所有样本一起被抽出或一起不抽，避免聚类内相关的样本
    被当作独立观测（V9 协议的统计要求）。
    """
    if len(x) != len(y) or len(x) != len(clusters):
        raise ValueError("x/y/clusters 长度必须一致")
    units: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        units.setdefault(c, []).append(i)
    unit_ids = sorted(units)
    if not unit_ids:
        return {}
    rng = random.Random(seed)
    diffs = [a - b for a, b in zip(x, y)]
    point = statistics.mean(diffs)
    samples = []
    for _ in range(reps):
        chosen = [i for u in (unit_ids[rng.randrange(len(unit_ids))]
                              for _ in range(len(unit_ids)))
                  for i in units[u]]
        if chosen:
            samples.append(statistics.mean(diffs[i] for i in chosen))
    samples.sort()
    return {"mean_diff": round(point, 4),
            "ci95": [round(samples[int(0.025 * reps)], 4),
                     round(samples[int(0.975 * reps)], 4)],
            "n": len(x), "n_clusters": len(unit_ids)}


def clustered_macro_f1_diff(truths: list[str], preds_x: list[str],
                            preds_y: list[str], clusters: list[str],
                            reps: int = 10000, seed: int = 42) -> dict:
    """Macro-F1 差值的聚类 bootstrap（点估计与区间同口径，V9.5 统一）。

    与 clustered_paired_diff 的关键区别：那个是对**逐样本命中率**（0/1）做
    均值差，指标口径与表格里的 Macro-F1 不是同一个统计量——V9.1/V9.5 两次
    审计都因此产生矛盾数字。本函数对"Macro-F1 本身"做聚类重采样差值：
    每次重采样日期簇后，分别对两套预测各算一次 Macro-F1，再取差。

    返回的 mean_diff 即全样本上的 Macro-F1 点估计差，与报告表格严格一致。
    """
    if not (len(truths) == len(preds_x) == len(preds_y) == len(clusters)):
        raise ValueError("truths/preds_x/preds_y/clusters 长度必须一致")
    units: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        units.setdefault(c, []).append(i)
    unit_ids = sorted(units)
    if not unit_ids:
        return {}
    rng = random.Random(seed)
    samples = []
    for _ in range(reps):
        chosen = sorted(i for u in (unit_ids[rng.randrange(len(unit_ids))]
                                    for _ in range(len(unit_ids)))
                        for i in units[u])
        if chosen:
            t = [truths[i] for i in chosen]
            samples.append(macro_f1(t, [preds_x[i] for i in chosen])
                           - macro_f1(t, [preds_y[i] for i in chosen]))
    samples.sort()
    point = macro_f1(truths, preds_x) - macro_f1(truths, preds_y)
    return {"metric": "macro_f1_diff", "mean_diff": round(point, 4),
            "ci95": [round(samples[int(0.025 * reps)], 4),
                     round(samples[int(0.975 * reps)], 4)],
            "n": len(truths), "n_clusters": len(unit_ids)}


def coverage_risk_curve(truths: list[str], preds: list[str],
                        abstain_label: str = "uncertain") -> list[dict]:
    """覆盖率—风险曲线：逐步把低置信判断折成弃判，看 F1 如何变化。

    防止通过大量弃判抬高 F1：报告 (覆盖率, F1) 全曲线而非单点。
    """
    curve = []
    n = len(truths)
    # 按预测的"把握"排序在这里不可得（无分数），因此报告各弃判比例下的
    # 随机弃判上界意义不大；改为按"预测类别条件覆盖率"扫描：
    # 依次放弃占比最小的非不确定预测类别，模拟逐步收紧的弃判策略。
    working = list(preds)
    curve.append({"coverage": round(sum(1 for p in working if p != abstain_label) / n, 4),
                  "macro_f1": round(macro_f1(truths, working), 4),
                  "abstain_rule": "none"})
    non_abstain_cats = [c for c in Counter(working) if c != abstain_label]
    while non_abstain_cats:
        smallest = min(non_abstain_cats, key=lambda c: Counter(working)[c])
        working = [abstain_label if p == smallest else p for p in working]
        non_abstain_cats = [c for c in Counter(working) if c != abstain_label]
        if all(p == abstain_label for p in working):
            break
        curve.append({"coverage": round(sum(1 for p in working if p != abstain_label) / n, 4),
                      "macro_f1": round(macro_f1(truths, working), 4),
                      "abstain_rule": f"drop {smallest}"})
    return curve
