# -*- coding: utf-8 -*-
"""出行评分与耗时模型的契约测试。

针对的是上一版模型被实测出来的三个缺陷：

1. **评分不可分解**：旧模型以"本次候选集里的最差方案"做归一化，
   同一条路线的得分会随"这次还比了哪几条"变化（实测偏移 2.4 分）。
   新模型只有一个绝对效用式，因此必须能在不知道候选集的情况下算出来。
2. **维度冗余**：旧模型把"走多远"拆成耗时／里程／天气暴露／超时四项，
   实测两两相关系数 0.94~0.99。新模型每一项都有独立含义，且相加即得总效用。
3. **跨方式剂量不可比**：旧的 Σ指数×公里 不含停留时间与呼吸量，
   步行与驾车在同一条街上会得到几乎相同的"剂量"。新模型按分段折算。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.route_tools import get_road_graph, path_junctions, search_route_alternatives
from tools.travel_advice_tools import (
    evaluate_option,
    load_travel_config,
    rail_minutes,
    resolve_profile,
    road_minutes,
    segments_profile,
)

CASE_ORIGIN = (114.17218, 22.29770)
CASE_DESTINATION = (114.16828, 22.32441)


@pytest.fixture(scope="module")
def config():
    return load_travel_config()


@pytest.fixture(scope="module")
def commuter(config):
    return resolve_profile(config, "commuter")


@pytest.fixture(scope="module")
def calm_weather():
    """无风无雨的中性天气，避免天气项掩盖要检验的性质。"""
    return {factor: 0.0 for factor in ("rain", "heat", "cold", "humidity", "wind", "air_quality")}


def make_segments(*, kind: str = "walk", minutes: float = 30.0, index: float = 0.2):
    return [{"kind": kind, "minutes": minutes, "mean_index": index}]


class TestUtilityIsDecomposable:
    """效用必须能在不知道候选集的情况下算出来。"""

    def test_components_sum_to_utility(self, config, commuter, calm_weather):
        segments = make_segments(minutes=25.0, index=0.3)
        result = evaluate_option(
            segments=segments,
            minutes=25.0,
            distance_km=2.0,
            mode="walk",
            profile=commuter,
            scaled=calm_weather,
            config=config,
        )
        detail = result["utility_detail"]
        component_sum = (
            detail["outdoor_equivalent_minutes"]
            + detail["weather_added_minutes"]
            + detail["pollution_added_minutes"]
            + detail["mode_constant_minutes"]
            + detail["overrun_minutes"]
            + detail["walk_overrun_minutes"]
        )
        assert result["utility_minutes"] == pytest.approx(component_sum, abs=0.05), (
            "各项之和必须等于总效用，否则排序依据与解释会对不上"
        )

    def test_same_option_scores_identically_in_any_context(self, config, commuter, calm_weather):
        """同一方案重复评估必须完全一致——不存在候选集相关的输入。"""
        segments = make_segments(minutes=20.0, index=0.15)
        first = evaluate_option(
            segments=segments, minutes=20.0, distance_km=1.5, mode="drive",
            profile=commuter, scaled=calm_weather, config=config,
        )
        second = evaluate_option(
            segments=segments, minutes=20.0, distance_km=1.5, mode="drive",
            profile=commuter, scaled=calm_weather, config=config,
        )
        assert first == second

    def test_score_is_monotone_in_utility(self, config, commuter, calm_weather):
        scores = []
        for minutes in (5.0, 20.0, 60.0, 120.0):
            result = evaluate_option(
                segments=make_segments(minutes=minutes),
                minutes=minutes,
                distance_km=minutes / 60.0 * 4.5,
                mode="walk",
                profile=commuter,
                scaled=calm_weather,
                config=config,
            )
            scores.append((result["utility_minutes"], result["score"]))
        ordered = sorted(scores)
        assert ordered == scores, "效用越大得分必须越低"
        assert scores[0][1] > scores[-1][1]
        assert all(0.0 <= score <= 100.0 for _, score in scores)


class TestIntakeIsModeComparable:
    """吸入当量必须能体现出行方式之间的差别，而不只是路面浓度。"""

    def test_driving_weighs_less_than_walking_on_the_same_street(self, config):
        walk = segments_profile(make_segments(kind="walk", minutes=30.0, index=0.3), config)
        drive = segments_profile(make_segments(kind="drive", minutes=30.0, index=0.3), config)
        assert walk["intake_index"] > drive["intake_index"] * 3, (
            "同样 30 分钟、同样路面浓度，步行的吸入量必须显著高于车内"
        )

    def test_intake_scales_with_exposure_and_time(self, config):
        base = segments_profile(make_segments(minutes=10.0, index=0.2), config)
        doubled_time = segments_profile(make_segments(minutes=20.0, index=0.2), config)
        doubled_index = segments_profile(make_segments(minutes=10.0, index=0.4), config)
        assert doubled_time["intake_index"] == pytest.approx(base["intake_index"] * 2, rel=0.02)
        assert doubled_index["intake_index"] == pytest.approx(base["intake_index"] * 2, rel=0.02)

    def test_zero_exposure_gives_zero_intake(self, config):
        result = segments_profile(make_segments(index=0.0), config)
        assert result["intake_index"] == 0.0

    def test_outdoor_equivalent_uses_enclosure(self, config):
        """轨道区间的户外等效时间要远小于实际时长。"""
        rail = segments_profile(make_segments(kind="transit_rail", minutes=30.0, index=0.5), config)
        walk = segments_profile(make_segments(kind="walk", minutes=30.0, index=0.5), config)
        assert rail["outdoor_equivalent_minutes"] < walk["outdoor_equivalent_minutes"] * 0.3


class TestRoadMinutes:
    """耗时不再是恒定速度乘里程。"""

    def test_junction_wait_adds_time(self, config):
        without = road_minutes(
            mode="walk", edges=None, distance_km=3.0, base_speed_kmh=4.5,
            junctions=0, hour=8, config=config,
        )
        with_wait = road_minutes(
            mode="walk", edges=None, distance_km=3.0, base_speed_kmh=4.5,
            junctions=20, hour=8, config=config,
        )
        assert with_wait["minutes"] > without["minutes"]
        assert with_wait["junction_wait_minutes"] > 0

    def test_peak_hour_is_slower_than_offpeak(self, config):
        edges = [{"length_km": 1.0, "route_number": ""}, {"length_km": 1.0, "route_number": ""}]
        peak = road_minutes(
            mode="drive", edges=edges, distance_km=2.0, base_speed_kmh=22.0,
            junctions=0, hour=8, config=config,
        )
        offpeak = road_minutes(
            mode="drive", edges=edges, distance_km=2.0, base_speed_kmh=22.0,
            junctions=0, hour=14, config=config,
        )
        assert peak["minutes"] > offpeak["minutes"]
        assert peak["congestion_factor"] < offpeak["congestion_factor"]

    def test_trunk_road_is_faster(self, config):
        trunk = road_minutes(
            mode="drive", edges=[{"length_km": 2.0, "route_number": "1"}],
            distance_km=2.0, base_speed_kmh=22.0, junctions=0, hour=14, config=config,
        )
        local = road_minutes(
            mode="drive", edges=[{"length_km": 2.0, "route_number": ""}],
            distance_km=2.0, base_speed_kmh=22.0, junctions=0, hour=14, config=config,
        )
        assert trunk["minutes"] < local["minutes"]

    def test_rail_minutes_increase_with_stops(self, config):
        few = rail_minutes(3.0, 2, config)
        many = rail_minutes(3.0, 8, config)
        assert many["minutes"] > few["minutes"]


class TestJunctionCounting:
    """路口数必须来自拓扑，而不是方位差噪声。"""

    def test_straight_road_is_not_dozens_of_turns(self):
        graph = get_road_graph()
        route = search_route_alternatives(
            graph, CASE_ORIGIN, CASE_DESTINATION, mode="walk", max_routes=1
        )[0]
        junctions = route["junctions"]
        assert 0 < junctions < len(route["edge_indices"]) / 2, (
            f"一条主干道上数出 {junctions} 个路口而路径只有 "
            f"{len(route['edge_indices'])} 条边，说明把中心线折点当成了路口"
        )

    def test_short_paths_have_no_junctions(self):
        graph = get_road_graph()
        assert path_junctions(graph, []) == 0
        assert path_junctions(graph, [0]) == 0


class TestDegenerateCases:
    """退化输入不能抛异常。"""

    def test_empty_segments(self, config, commuter, calm_weather):
        result = evaluate_option(
            segments=[], minutes=0.0, distance_km=0.0, mode="walk",
            profile=commuter, scaled=calm_weather, config=config,
        )
        assert result["intake_detail"]["intake_index"] == 0.0
        assert result["utility_minutes"] >= 0.0

    def test_long_trip_is_penalised_by_overrun(self, config, commuter, calm_weather):
        """超出画像单程上限的方案必须被罚到明显更差。"""
        short = evaluate_option(
            segments=make_segments(minutes=20.0), minutes=20.0, distance_km=1.0,
            mode="walk", profile=commuter, scaled=calm_weather, config=config,
        )
        long = evaluate_option(
            segments=make_segments(minutes=90.0), minutes=90.0, distance_km=1.0,
            mode="walk", profile=commuter, scaled=calm_weather, config=config,
        )
        assert long["utility_detail"]["overrun_minutes"] > 0
        assert long["utility_minutes"] > short["utility_minutes"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
