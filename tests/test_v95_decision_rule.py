# -*- coding: utf-8 -*-
"""V9.5 决策规则（加权和 vs 画像驱动 Tchebycheff）单元测试。

用纯逻辑的合成候选集验证三件事：
  1. 加权和会被"一个目标极差、另一个目标极优"的候选拉动，Tchebycheff 不会；
  2. Pareto 非支配护栏在两种规则下都生效——被严格支配的候选绝不推荐；
  3. 画像驱动：时间压力大的画像更偏向时间，弹性大的画像更偏向暴露。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))

from tools.travel_advice_tools import (  # noqa: E402
    profile_time_pressure, select_recommendation, tchebycheff_pick)


def _option(route_id: str, minutes: float, intake: float,
            utility: float | None = None) -> dict:
    return {"route_id": route_id, "duration_minutes": minutes,
            "intake_index": intake,
            "utility_minutes": utility if utility is not None else minutes + 8.0 * intake}


CONFIG_TCHEB = {"decision": {"rule": "tchebycheff", "epsilon": 1e-9,
                              "reference_minutes": 75.0}}
CONFIG_WS = {"decision": {"rule": "weighted_sum", "epsilon": 1e-9,
                          "reference_minutes": 75.0}}

COMMUTER = {"profile_id": "commuter", "max_trip_minutes": 25,
            "time_flexibility_minutes": 10}
RUNNER = {"profile_id": "runner", "max_trip_minutes": 60,
          "time_flexibility_minutes": 120}


def test_profile_time_pressure_order() -> None:
    assert profile_time_pressure(COMMUTER) > profile_time_pressure(RUNNER)
    assert 0.0 < profile_time_pressure(COMMUTER) < 1.0


def test_weighted_sum_takes_extreme_tchebycheff_balances() -> None:
    """加权和选"极快但极脏"，Tchebycheff 选折中——这正是两者的差异所在。"""
    fast_dirty = _option("fast", 10.0, 1.00, utility=10.0 + 8.0 * 1.00)   # 快但脏
    balanced = _option("mid", 14.0, 0.50, utility=14.0 + 8.0 * 0.50)       # 折中
    slow_clean = _option("slow", 22.0, 0.45, utility=22.0 + 8.0 * 0.45)    # 慢但净
    options = [fast_dirty, balanced, slow_clean]
    for o in options:
        o["pareto_dominated"] = False

    ws = select_recommendation(options, COMMUTER, CONFIG_WS)
    tc = select_recommendation(options, COMMUTER, CONFIG_TCHEB)
    assert ws["route_id"] == "fast", "加权和按效用最小选最快（最脏）"
    assert tc["route_id"] == "mid", "Tchebycheff 取折中，不让极差目标被补偿"


def test_pareto_guard_blocks_dominated_option() -> None:
    """被严格支配的候选即使效用最低也不能被推荐。"""
    dominated = _option("bad", 12.0, 0.80, utility=1.0)   # 人为给一个极小效用
    dominated["pareto_dominated"] = True
    good = _option("good", 12.0, 0.60, utility=12.0 + 8.0 * 0.60)
    good["pareto_dominated"] = False
    for config in (CONFIG_WS, CONFIG_TCHEB):
        picked = select_recommendation([dominated, good], COMMUTER, config)
        assert picked["route_id"] == "good", f"{config['decision']['rule']} 不得选被支配方案"


def test_profile_driven_weights_flip_choice() -> None:
    """同一候选集：时间压力大的画像偏时间，弹性大的画像偏暴露。"""
    # 两个非支配候选：A 快但暴露略高；B 慢一点但暴露明显更低
    a = _option("A", 12.0, 0.70)
    b = _option("B", 16.0, 0.30)
    for o in (a, b):
        o["pareto_dominated"] = False
    commuter_pick = tchebycheff_pick([a, b], COMMUTER, CONFIG_TCHEB)
    runner_pick = tchebycheff_pick([a, b], RUNNER, CONFIG_TCHEB)
    assert commuter_pick["route_id"] == "A"
    assert runner_pick["route_id"] == "B"


def test_tchebycheff_returns_none_on_empty() -> None:
    assert tchebycheff_pick([], COMMUTER, CONFIG_TCHEB) is None
    assert select_recommendation([], COMMUTER, CONFIG_TCHEB) is None
