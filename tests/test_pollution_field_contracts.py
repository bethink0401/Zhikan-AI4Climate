# -*- coding: utf-8 -*-
"""污染暴露场的契约测试。

覆盖三类曾经出过问题、且容易被静默破坏的性质：

1. **阈值连续性**：静风与有风分支之间必须是平滑过渡。
   历史上用 `wind < 0.5` 硬判断，风速 0.49→0.51（差 0.02 m/s）会让行程区
   暴露中位数从 0.636 掉到 0.090，差 7 倍。
2. **不饱和**：静风分支必须保留空间梯度。全部路段顶到 1.0 时，
   避污搜索的所有候选成本相同，"避开污染"会在不报错的情况下失效。
3. **两条计算路径一致**：网格路径（numpy，正常）与解析路径（纯 Python，回退）
   必须给出同一个场值，否则无 numpy 的环境会静默得到另一个模型。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.pollution_field as pf
from spatial_agent.tools.spatial_tools import haversine_km
from tools.pollution_field import build_field_from_sources, load_pollution_rules
from tools.route_tools import get_road_graph

SOURCE_LON, SOURCE_LAT = 114.1694, 22.3193
CASE_ORIGIN = (114.17218, 22.29770)
CASE_DESTINATION = (114.16828, 22.32441)
CASE_WIND_FROM = 201.5
CASE_WIND_SPEED = 1.1925
CASE_STATION_STRENGTH = 0.94
CASE_TRAFFIC_STRENGTH = 0.7363

#: 行程相关区域：起终点 1.2 km 内的路段，也就是"实际要走的那些路"。
#: 用整个网格会掩盖问题——网格边缘的低值会把饱和比例摊薄。
TRIP_RADIUS_KM = 1.2

#: 相邻风速步长之间，行程区暴露中位数允许的最大相对跳变。
#: 定性判据：修复前的开关式判断在这一带给出 83% 的跳变，量级差 8 倍以上。
CONTINUITY_LIMIT = 0.10


@pytest.fixture(scope="module")
def road_graph():
    return get_road_graph()


@pytest.fixture(scope="module")
def corridor_edges(road_graph):
    scope_km = float(load_pollution_rules()["source_strength"]["corridor_scope_km"])
    return [
        edge
        for edge in road_graph.edges
        if haversine_km(SOURCE_LON, SOURCE_LAT, edge["mid_lon"], edge["mid_lat"]) <= scope_km
    ]


@pytest.fixture(scope="module")
def trip_edges(road_graph):
    return [
        edge
        for edge in road_graph.edges
        if min(
            haversine_km(CASE_ORIGIN[0], CASE_ORIGIN[1], edge["mid_lon"], edge["mid_lat"]),
            haversine_km(CASE_DESTINATION[0], CASE_DESTINATION[1], edge["mid_lon"], edge["mid_lat"]),
        )
        <= TRIP_RADIUS_KM
    ]


def build_field(corridor_edges, *, wind_speed, wind_from=CASE_WIND_FROM, rules=None):
    """构造案例场景的暴露场。清掉网格缓存，保证换风速时真的重算。"""
    pf._GRID_CACHE.clear()
    return build_field_from_sources(
        stations=[{"name": "Mong Kok Roadside", "lon": SOURCE_LON, "lat": SOURCE_LAT}],
        station_strength=CASE_STATION_STRENGTH,
        corridor_edges=corridor_edges,
        corridor_strength=CASE_TRAFFIC_STRENGTH,
        wind_from_direction=wind_from,
        wind_speed_ms=wind_speed,
        focus_points=[CASE_ORIGIN, CASE_DESTINATION],
        rules=rules,
    )


def trip_exposures(field, trip_edges):
    return [field.index_at(edge["mid_lon"], edge["mid_lat"]) for edge in trip_edges]


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


class TestCalmTransitionContinuity:
    """静风分支与烟羽分支之间必须是平滑过渡，不能是开关。"""

    def test_no_step_change_across_transition(self, corridor_edges, trip_edges):
        rules = load_pollution_rules()
        threshold = float(rules["calm"]["threshold_ms"])
        transition = float(rules["calm"]["transition_ms"])

        winds = []
        step = 0.03
        value = threshold - 0.09
        while value <= transition + 0.09:
            winds.append(round(value, 4))
            value += step

        medians = [
            median(trip_exposures(build_field(corridor_edges, wind_speed=wind), trip_edges))
            for wind in winds
        ]

        worst_ratio, worst_pair = 0.0, None
        for index in range(1, len(medians)):
            previous, current = medians[index - 1], medians[index]
            if previous <= 0:
                continue
            ratio = abs(current - previous) / previous
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst_pair = (winds[index - 1], winds[index])

        assert worst_ratio < CONTINUITY_LIMIT, (
            f"风速在 {worst_pair} 之间出现 {worst_ratio * 100:.0f}% 的跳变，"
            f"超过 {CONTINUITY_LIMIT * 100:.0f}% 上限：静风与烟羽分支没有平滑衔接"
        )

    def test_calm_weight_is_monotonic(self, corridor_edges):
        rules = load_pollution_rules()
        threshold = float(rules["calm"]["threshold_ms"])
        transition = float(rules["calm"]["transition_ms"])
        weights = [
            build_field(corridor_edges, wind_speed=wind).calm_weight
            for wind in (
                threshold - 0.1,
                threshold,
                (threshold + transition) / 2.0,
                transition,
                transition + 0.5,
            )
        ]
        assert weights == sorted(weights, reverse=True), "calm_weight 必须随风速单调递减"
        assert weights[0] == 1.0
        assert weights[-1] == 0.0


class TestCalmBranchDoesNotSaturate:
    """静风分支必须保留空间梯度，否则避污搜索失去区分度。"""

    @pytest.mark.parametrize("wind_speed", [0.2, 0.35, 0.5])
    def test_no_saturated_segments(self, corridor_edges, trip_edges, wind_speed):
        field = build_field(corridor_edges, wind_speed=wind_speed)
        exposures = trip_exposures(field, trip_edges)
        saturated = [value for value in exposures if value >= 0.999]
        share = len(saturated) / len(exposures)
        assert share < 0.05, (
            f"风速 {wind_speed} m/s 时行程区有 {share * 100:.1f}% 的路段暴露饱和到 1.0，"
            "避污成本会全部相同而无从选择"
        )

    def test_corridor_gradient_survives(self, corridor_edges):
        field = build_field(corridor_edges, wind_speed=0.3)
        hotspot = field.index_at(SOURCE_LON, SOURCE_LAT)
        parallel_street = field.index_at(114.16760, 22.31600)
        far_field = field.index_at(114.17218, 22.29770)
        assert hotspot > parallel_street > far_field, "暴露应随离源距离单调下降"
        assert hotspot / max(parallel_street, 1e-6) > 1.3, (
            "静风分支下主干道与平行街的暴露差距过小，绕行不会有任何收益"
        )
        assert far_field < 0.05, "远离源的位置暴露应接近 0"


class TestGridMatchesAnalyticPath:
    """网格路径与解析路径必须给出同一个场值。"""

    @pytest.mark.parametrize("wind_speed", [0.3, 0.9, CASE_WIND_SPEED, 3.0])
    def test_paths_agree(self, road_graph, corridor_edges, wind_speed):
        field = build_field(corridor_edges, wind_speed=wind_speed)
        field.index_at(SOURCE_LON, SOURCE_LAT)

        random.seed(3)
        sample = random.sample(road_graph.edges, 120)
        pairs = [
            (
                field.index_at(edge["mid_lon"], edge["mid_lat"]),
                field._plume_at(edge["mid_lon"], edge["mid_lat"]),
            )
            for edge in sample
        ]
        worst = max(abs(a - b) for a, b in pairs)
        # 双线性插值误差随场幅值线性放大（plume_scale 标定改变场量级），
        # 容差按场峰值相对化：插值误差 ≤ 场峰值的 4%（原绝对容差 0.02 对应
        # 旧标定下约 4% 的峰值）。
        peak = max(max(a, b) for a, b in pairs) or 1.0
        assert worst < 0.04 * peak, (
            f"风速 {wind_speed} m/s 时网格与解析路径最大差 {worst:.4f}"
            f"（场峰值 {peak:.3f}，相对差 {worst / peak:.1%}），"
            "两条路径已经分叉（差值应仅来自双线性插值）"
        )


class TestMissingWindDegradesExplicitly:
    """缺少风速时不能退回静风假设，而要显式不建场。"""

    def test_agent_skips_field_without_wind(self):
        from agents.travel_agent import TravelAgent

        meteorology = {
            "status": "success",
            "result": {
                "weather_risk": 0.7237,
                "risk_type": "湿热静稳",
                "dispersion_condition": "poor",
                "mean_wind_speed": None,
                "mean_humidity": 84.75,
                "mean_temperature": 28.5,
                "total_precipitation": 0.0,
                "dominant_wind_direction": None,
                "hourly_count": 3,
                "hourly_series": [],
            },
        }
        task = {
            "task_id": "TEST_NO_WIND",
            "origin": {"name": "尖沙咀站", "lat": 22.29770, "lon": 114.17218},
            "destination": {"name": "太子站", "lat": 22.32441, "lon": 114.16828},
            "start_time": "2023-08-15 08:00:00",
            "end_time": "2023-08-15 11:00:00",
            "travel_profile": "commuter",
            "pollution_source": {"station_id": "MK", "lat": SOURCE_LAT, "lon": SOURCE_LON},
            "air_quality_risk": 0.94,
        }

        result = TravelAgent().run(task, meteorology_result=meteorology)
        payload = result["result"]

        assert payload["pollution_field"] is None, "缺风速时不应建立暴露场"
        assert payload["pollution_advice"]["available"] is False
        assert any("风速" in warning for warning in result["warnings"]), (
            "缺风速必须给出明确警告，而不是静默降级"
        )
        doses = [option["exposure"]["dose_index"] for option in payload["route_options"]]
        assert all(dose == 0 for dose in doses), "无暴露场时不应伪造成剂量数值"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
