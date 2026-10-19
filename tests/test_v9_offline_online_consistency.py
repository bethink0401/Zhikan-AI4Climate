# -*- coding: utf-8 -*-
"""V9-P0 回归：离线 D 臂决策与在线系统决策在相同约束下必须一致。

背景（V8 消融 57/60 的根因）：原评测器拿"带暴露上限的离线决策"对比
"未传 user_constraints 的系统推荐"——当效用最优候选（驾车最快）的 EU
超过 cap=1.25×最优 EU 时，离线将其排除而在线保留，产生 3/60 假性
不一致（全部是跨海 OD 的 commuter/parent 案例）。

修复后的不变量：**相同 cap 下**，离线 decision_zhikan 与系统在线逻辑
（约束过滤 → 效用排序 → Pareto 非支配 → 取首）必须逐例一致。
本测试用随机构造的候选集（含"最优效用候选 EU 超 cap"的对抗形状，
即 V8 三例不一致的特征）对两条决策路径做等价性检验。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "benchmarks"))

from bench_travel_v8 import decision_zhikan  # noqa: E402


def _make_candidate(rng: random.Random, mode: str, index: int) -> dict:
    """构造一条候选：时间与 EU 负相关趋势 + 噪声，效用与 V8 口径一致。"""
    duration = round(rng.uniform(8.0, 40.0), 2)
    intake = round(max(0.1, rng.uniform(0.2, 1.2) - 0.02 * (40.0 - duration) / 8.0), 4)
    return {
        "mode": mode,
        "route_id": f"{mode}_{index}",
        "duration_minutes": duration,
        "intake_index": intake,
        "utility_minutes": round(duration + 8.0 * intake, 4),
    }


def _reference_online_decision(candidates: list[dict], cap: float) -> dict | None:
    """系统在线决策的参考实现（与 travel_agent 的约束→排序→Pareto→取首一致）。"""
    feasible = [c for c in candidates if c["intake_index"] <= cap]
    if not feasible:
        return None
    ordered = sorted(feasible, key=lambda c: (c["utility_minutes"], c["duration_minutes"]))
    for option in ordered:
        dominated = any(
            other is not option
            and other["duration_minutes"] <= option["duration_minutes"]
            and other["intake_index"] <= option["intake_index"]
            and (other["duration_minutes"] < option["duration_minutes"]
                 or other["intake_index"] < option["intake_index"])
            for other in feasible)
        if not dominated:
            return option
    return None


def _adversarial_case(rng: random.Random) -> list[dict]:
    """V8 三例不一致的特征形状：最快驾车候选 EU 超过 cap。"""
    fast_dirty = {"mode": "drive", "route_id": "drive_fast",
                  "duration_minutes": 13.5, "intake_index": 0.60,
                  "utility_minutes": 18.3}
    slow_clean = {"mode": "transit", "route_id": "transit_0",
                  "duration_minutes": 37.1, "intake_index": 0.45,
                  "utility_minutes": 40.7}
    balanced = {"mode": "drive", "route_id": "drive_bal",
                "duration_minutes": 20.0, "intake_index": 0.50,
                "utility_minutes": 24.0}
    extra = _make_candidate(rng, "walk", 0)
    return [fast_dirty, slow_clean, balanced, extra]


class TestOfflineOnlineDecisionConsistency:
    CAP_RATIO = 1.25

    def test_adversarial_cap_case_matches(self):
        """V8 假性不一致的特征场景：两臂在同一 cap 下必须选同一条。"""
        candidates = _adversarial_case(random.Random(7))
        cap = self.CAP_RATIO * min(c["intake_index"] for c in candidates)
        offline = decision_zhikan(candidates, cap)
        online = _reference_online_decision(candidates, cap)
        # cap 排除最快最脏的 drive_fast；两臂都应在剩余可行集里选效用最优
        assert offline is not None and online is not None
        assert offline["route_id"] == online["route_id"], (
            "相同 cap 下离线与在线决策不一致——V8 消融的评测缺陷回归"
        )
        # 无约束时系统会选择 drive_fast（这正是原 3/60 假差异的来源）
        unconstrained = _reference_online_decision(candidates, float("inf"))
        assert unconstrained["route_id"] == "drive_fast"

    @pytest.mark.parametrize("seed", range(30))
    def test_randomized_cases_match(self, seed):
        """随机构造 30 组候选集，两臂逐例一致（含并列效用形状）。"""
        rng = random.Random(seed)
        n = rng.randint(3, 8)
        modes = ["walk", "drive", "transit"]
        candidates = [_make_candidate(rng, modes[i % 3], i) for i in range(n)]
        # 随机制造并列效用
        if n >= 4 and rng.random() < 0.5:
            candidates[-1]["utility_minutes"] = candidates[0]["utility_minutes"]
        cap = self.CAP_RATIO * min(c["intake_index"] for c in candidates)
        offline = decision_zhikan(candidates, cap)
        online = _reference_online_decision(candidates, cap)
        if offline is None or online is None:
            assert offline is online
        else:
            assert (offline["route_id"], offline["mode"]) == \
                   (online["route_id"], online["mode"]), (
                f"seed={seed}: 离线 {offline['route_id']} vs 在线 {online['route_id']}")
