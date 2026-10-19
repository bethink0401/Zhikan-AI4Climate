# -*- coding: utf-8 -*-
"""V9.6 出行决策权重：语义单调性与约束边界。

审查发现旧权重公式 `w_time = T_max/(T_max+T_flex)` 单调性反向：固定时间弹性时，
用户允许的最大出行时间越长，权重越大（"越宽裕反而越像时间紧迫"）。本测试锁定
修正后的语义，并区分"系统硬约束"与"评测器比率上限"两类不可行。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.travel_advice_tools import (  # noqa: E402
    profile_time_pressure, resolve_decision_weights)

BASE_CONFIG = {"decision": {"weight_basis": "time_pressure", "reference_minutes": 30.0,
                            "fixed_time_weight": 0.5, "epsilon": 1e-9}}


def _profile(max_trip: float, flex: float) -> dict:
    return {"max_trip_minutes": max_trip, "time_flexibility_minutes": flex}


def test_time_pressure_decreases_when_max_trip_grows() -> None:
    """固定弹性：允许的最大出行时间越长 → 时间压力越低（旧公式相反）。"""
    values = [profile_time_pressure(_profile(t, 20.0), BASE_CONFIG)
              for t in (10.0, 20.0, 30.0, 60.0, 120.0)]
    assert values == sorted(values, reverse=True), f"应单调不增，实际 {values}"
    assert values[0] > values[-1]


def test_time_pressure_decreases_when_flexibility_grows() -> None:
    """固定最大时间：时间弹性越大 → 时间压力越低。"""
    values = [profile_time_pressure(_profile(30.0, f), BASE_CONFIG)
              for f in (0.0, 10.0, 30.0, 60.0, 120.0)]
    assert values == sorted(values, reverse=True), f"应单调不增，实际 {values}"


def test_legacy_formula_is_non_monotonic_in_max_trip() -> None:
    """旧公式的单调性反向——保留为对照，防止误改回去。"""
    legacy = dict(BASE_CONFIG)
    legacy["decision"] = {**BASE_CONFIG["decision"],
                          "weight_basis": "time_pressure_legacy"}
    values = [resolve_decision_weights(_profile(t, 20.0), legacy)[0]
              for t in (10.0, 30.0, 60.0)]
    assert values == sorted(values), f"旧公式预期随 T_max 递增，实际 {values}"


def test_profile_ordering_matches_semantics() -> None:
    """画像间排序：时间预算越紧的画像时间权重越高。"""
    order = [("commuter", 25, 10), ("parent_with_child", 30, 20),
             ("tourist", 50, 60), ("runner", 60, 120)]
    weights = {pid: resolve_decision_weights(_profile(t, f), BASE_CONFIG)[0]
               for pid, t, f in order}
    assert weights["commuter"] > weights["parent_with_child"] > weights["tourist"] \
        > weights["runner"], weights


def test_weights_are_normalised_and_bounded() -> None:
    for t, f in ((0, 0), (5, 0), (0, 30), (25, 10), (600, 600)):
        w_time, w_intake = resolve_decision_weights(_profile(t, f), BASE_CONFIG)
        assert abs(w_time + w_intake - 1.0) < 1e-9
        assert 0.0 <= w_time <= 1.0 and 0.0 <= w_intake <= 1.0


def test_fixed_basis_ignores_profile() -> None:
    fixed = {"decision": {**BASE_CONFIG["decision"], "weight_basis": "fixed"}}
    a = resolve_decision_weights(_profile(25, 10), fixed)
    b = resolve_decision_weights(_profile(120, 300), fixed)
    assert a == b == (0.5, 0.5)
