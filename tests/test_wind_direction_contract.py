"""测试 MeteorologyAgent 与 SpatialAgent 风向字段契约"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spatial_agent.agents.spatial_agent import SpatialAgent, _get_valid_wind_direction


# ---------------------------------------------------------------------------
# 测试 _get_valid_wind_direction 工具函数
# ---------------------------------------------------------------------------

class TestGetValidWindDirection:
    """测试风向提取函数的优先级和边界条件"""

    def test_dominant_wind_direction_first(self):
        """优先读取 dominant_wind_direction"""
        data = {
            "dominant_wind_direction": 201.5,
            "latest_wind_direction": 180.0,
            "wind_direction": 90.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 201.5
        assert source == "dominant_wind_direction"

    def test_fallback_to_latest_wind_direction(self):
        """dominant 缺失时回退到 latest_wind_direction"""
        data = {
            "dominant_wind_direction": None,
            "latest_wind_direction": 180.0,
            "wind_direction": 90.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 180.0
        assert source == "latest_wind_direction"

    def test_fallback_to_wind_direction(self):
        """dominant 和 latest 都缺失时回退到旧字段 wind_direction"""
        data = {
            "wind_direction": 90.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 90.0
        assert source == "wind_direction"

    def test_zero_degree_is_valid(self):
        """0° 是合法北风，不应被当作缺失值"""
        data = {
            "dominant_wind_direction": 0.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 0.0
        assert source == "dominant_wind_direction"

    def test_all_missing_returns_none(self):
        """所有风向字段都缺失时返回 None"""
        data = {}
        value, source = _get_valid_wind_direction(data)
        assert value is None
        assert source is None

    def test_invalid_type_skipped(self):
        """无法转换为 float 的值被跳过"""
        data = {
            "dominant_wind_direction": "invalid",
            "latest_wind_direction": 180.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 180.0
        assert source == "latest_wind_direction"

    def test_out_of_range_skipped(self):
        """超出 [0, 360) 范围的值被跳过"""
        data = {
            "dominant_wind_direction": 361,
            "latest_wind_direction": 180.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 180.0
        assert source == "latest_wind_direction"

    def test_negative_value_skipped(self):
        """负值被跳过"""
        data = {
            "dominant_wind_direction": -10,
            "latest_wind_direction": 180.0,
        }
        value, source = _get_valid_wind_direction(data)
        assert value == 180.0
        assert source == "latest_wind_direction"


# ---------------------------------------------------------------------------
# 测试 SpatialAgent 与 MeteorologyAgent 集成
# ---------------------------------------------------------------------------

class TestSpatialMeteorologyIntegration:
    """测试 SpatialAgent 正确接收和使用 MeteorologyAgent 输出"""

    @pytest.fixture
    def sample_task(self):
        """标准测试任务"""
        return {
            "task_id": "TEST_MK_001",
            "station_id": "MK",
            "start_time": "2023-08-15 08:00:00",
            "end_time": "2023-08-15 11:00:00",
            "lat": 22.3227,
            "lon": 114.1683,
        }

    @pytest.fixture
    def meteo_result_with_dominant(self):
        """包含 dominant_wind_direction 的气象结果"""
        return {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "dominant_wind_direction": 201.5,
                "dominant_wind_direction_label": "SSW",
                "latest_wind_direction": 201.0,
                "upwind_direction": 201.5,
                "downwind_direction": 21.5,
                "mean_wind_speed": 1.2,
            },
        }

    def test_spatial_uses_dominant_wind_direction(self, sample_task, meteo_result_with_dominant):
        """SpatialAgent 应优先使用 dominant_wind_direction"""
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result_with_dominant)

        assert result["status"] == "success"
        assert result["meta"]["wind_direction_used"] == 201.5
        assert result["meta"]["wind_direction_source"] == "dominant_wind_direction"
        assert len(result["warnings"]) == 0
        assert "wind_direction is missing" not in str(result)

    def test_spatial_generates_upwind_roads(self, sample_task, meteo_result_with_dominant):
        """有效风向应生成上风向道路列表"""
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result_with_dominant)

        assert len(result["result"]["upwind_roads"]) > 0
        assert "downwind sector" in result["result"]["affected_area"]
        assert "buffer" not in result["result"]["affected_area"].lower()

    def test_spatial_without_dominant_uses_latest(self, sample_task):
        """dominant 缺失时使用 latest_wind_direction"""
        meteo_result = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "latest_wind_direction": 180.0,
                "mean_wind_speed": 1.5,
            },
        }
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result)

        assert result["status"] == "success"
        assert result["meta"]["wind_direction_used"] == 180.0
        assert result["meta"]["wind_direction_source"] == "latest_wind_direction"

    def test_spatial_missing_wind_direction_warning(self, sample_task):
        """风向完全缺失时应降级为圆形缓冲区"""
        meteo_result = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "mean_wind_speed": 1.5,
            },
        }
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result)

        assert result["status"] == "warning"
        assert any("wind_direction is missing" in w for w in result["warnings"])
        assert "buffer" in result["result"]["affected_area"].lower()
        assert len(result["result"]["upwind_roads"]) == 0

    def test_spatial_zero_degree_valid(self, sample_task):
        """0° 风向应被正常处理"""
        meteo_result = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "dominant_wind_direction": 0.0,
                "mean_wind_speed": 2.0,
            },
        }
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result)

        assert result["status"] == "success"
        assert result["meta"]["wind_direction_used"] == 0.0
        assert result["meta"]["wind_direction_source"] == "dominant_wind_direction"
        assert len(result["result"]["upwind_roads"]) > 0

    def test_spatial_handles_nested_result(self, sample_task):
        """SpatialAgent 应能从嵌套的 result 字段中提取风向"""
        meteo_result = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "dominant_wind_direction": 90.0,
                "mean_wind_speed": 1.8,
            },
        }
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result)

        assert result["status"] == "success"
        assert result["meta"]["wind_direction_used"] == 90.0

    def test_spatial_invalid_wind_direction(self, sample_task):
        """无效风向值（超出范围）应触发降级"""
        meteo_result = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "dominant_wind_direction": 400,  # 超出 360
                "mean_wind_speed": 1.5,
            },
        }
        agent = SpatialAgent()
        result = agent.run(sample_task, meteo_result)

        assert result["status"] == "warning"
        assert any("wind_direction is missing" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Coordinator 端到端测试
# ---------------------------------------------------------------------------

class TestCoordinatorWindDirectionContract:
    """测试 Coordinator 正确传递风向数据"""

    def test_coordinator_passes_meteorology_to_spatial(self):
        """Coordinator 应将 MeteorologyAgent 输出正确传给 SpatialAgent"""
        from agents.coordinator_min import CoordinatorMin

        task = {
            "task_id": "TEST_COORD_001",
            "station_id": "MK",
            "station_name": "Mong Kok Roadside",
            "start_time": "2023-08-15 08:00:00",
            "end_time": "2023-08-15 11:00:00",
            "lat": 22.3227,
            "lon": 114.1683,
        }

        coordinator = CoordinatorMin()
        result = coordinator.analyze(task)

        # 验证 MeteorologyAgent 输出风向
        meteo = result["meteorology"]
        assert meteo["status"] in ("success", "warning")
        meteo_result = meteo.get("result", {})
        assert "dominant_wind_direction" in meteo_result

        # 验证 SpatialAgent 接收风向
        spatial = result["spatial"]
        if meteo_result.get("dominant_wind_direction") is not None:
            assert spatial["status"] == "success"
            assert "meta" in spatial
            assert spatial["meta"]["wind_direction_source"] == "dominant_wind_direction"
            assert len(spatial["result"]["upwind_roads"]) > 0

    def test_coordinator_upwind_match_not_default(self):
        """有效风向时 SourceAgent 不应使用默认 upwind_match"""
        from agents.coordinator_min import CoordinatorMin

        task = {
            "task_id": "TEST_COORD_002",
            "station_id": "MK",
            "station_name": "Mong Kok Roadside",
            "start_time": "2023-08-15 08:00:00",
            "end_time": "2023-08-15 11:00:00",
            "lat": 22.3227,
            "lon": 114.1683,
        }

        coordinator = CoordinatorMin()
        result = coordinator.analyze(task)

        source = result["source"]
        upwind_match = source.get("result", {}).get("component_scores", {}).get("upwind_match")

        # 如果 Spatial 成功且有风向，upwind_match 不应是默认值 0.5
        if result["spatial"]["status"] == "success":
            assert upwind_match != 0.5, "upwind_match 不应使用默认值 0.5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
