#!/usr/bin/env python3
"""简单测试风向契约修复（无需 pytest）"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spatial_agent.agents.spatial_agent import SpatialAgent, _get_valid_wind_direction


def test_get_valid_wind_direction():
    """测试风向提取函数"""
    print("测试 1: 优先读取 dominant_wind_direction")
    data = {
        "dominant_wind_direction": 201.5,
        "latest_wind_direction": 180.0,
        "wind_direction": 90.0,
    }
    value, source = _get_valid_wind_direction(data)
    assert value == 201.5 and source == "dominant_wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 2: 回退到 latest_wind_direction")
    data = {"latest_wind_direction": 180.0, "wind_direction": 90.0}
    value, source = _get_valid_wind_direction(data)
    assert value == 180.0 and source == "latest_wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 3: 回退到旧字段 wind_direction")
    data = {"wind_direction": 90.0}
    value, source = _get_valid_wind_direction(data)
    assert value == 90.0 and source == "wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 4: 0° 是合法风向")
    data = {"dominant_wind_direction": 0.0}
    value, source = _get_valid_wind_direction(data)
    assert value == 0.0 and source == "dominant_wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 5: 全部缺失返回 None")
    data = {}
    value, source = _get_valid_wind_direction(data)
    assert value is None and source is None, f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 6: 无效类型被跳过")
    data = {"dominant_wind_direction": "invalid", "latest_wind_direction": 180.0}
    value, source = _get_valid_wind_direction(data)
    assert value == 180.0 and source == "latest_wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")

    print("测试 7: 超出范围被跳过")
    data = {"dominant_wind_direction": 361, "latest_wind_direction": 180.0}
    value, source = _get_valid_wind_direction(data)
    assert value == 180.0 and source == "latest_wind_direction", f"失败: {value}, {source}"
    print("  ✓ 通过")


def test_spatial_agent_integration():
    """测试 SpatialAgent 集成"""
    task = {
        "task_id": "TEST_001",
        "station_id": "MK",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3227,
        "lon": 114.1683,
    }

    print("\n测试 8: SpatialAgent 使用 dominant_wind_direction")
    meteo = {
        "agent_name": "MeteorologyAgent",
        "status": "success",
        "result": {
            "dominant_wind_direction": 201.5,
            "mean_wind_speed": 1.2,
        },
    }
    agent = SpatialAgent()
    result = agent.run(task, meteo)
    assert result["status"] == "success", f"失败: {result['status']}"
    assert result["meta"]["wind_direction_used"] == 201.5, f"失败: {result['meta']}"
    assert result["meta"]["wind_direction_source"] == "dominant_wind_direction"
    assert len(result["warnings"]) == 0
    assert len(result["result"]["upwind_roads"]) > 0
    print(f"  ✓ 通过: 使用 201.5°, upwind_roads={len(result['result']['upwind_roads'])}")

    print("测试 9: 缺失风向时降级")
    meteo_no_wind = {
        "agent_name": "MeteorologyAgent",
        "status": "success",
        "result": {
            "mean_wind_speed": 1.5,
        },
    }
    result = agent.run(task, meteo_no_wind)
    assert result["status"] == "warning", f"失败: {result['status']}"
    assert any("wind_direction is missing" in w for w in result["warnings"])
    assert len(result["result"]["upwind_roads"]) == 0
    print("  ✓ 通过: 降级为圆形缓冲区")

    print("测试 10: 0° 风向有效")
    meteo_zero = {
        "agent_name": "MeteorologyAgent",
        "status": "success",
        "result": {
            "dominant_wind_direction": 0.0,
            "mean_wind_speed": 2.0,
        },
    }
    result = agent.run(task, meteo_zero)
    assert result["status"] == "success", f"失败: {result['status']}"
    assert result["meta"]["wind_direction_used"] == 0.0
    assert len(result["result"]["upwind_roads"]) > 0
    print("  ✓ 通过: 0° 被正确识别为北风")


def test_coordinator_integration():
    """测试 Coordinator 端到端"""
    from agents.coordinator_min import CoordinatorMin

    task = {
        "task_id": "TEST_COORD",
        "station_id": "MK",
        "station_name": "Mong Kok Roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3227,
        "lon": 114.1683,
    }

    print("\n测试 11: Coordinator 完整链路")
    coordinator = CoordinatorMin()
    result = coordinator.analyze(task)

    meteo = result["meteorology"]
    spatial = result["spatial"]
    source = result["source"]

    assert meteo["status"] in ("success", "warning"), f"Meteo 失败: {meteo['status']}"
    assert "dominant_wind_direction" in meteo.get("result", {}), "Meteo 未输出 dominant_wind_direction"
    
    if meteo["result"].get("dominant_wind_direction") is not None:
        assert spatial["status"] == "success", f"Spatial 失败: {spatial['status']}"
        assert "meta" in spatial, "Spatial 未输出 meta"
        assert spatial["meta"]["wind_direction_source"] == "dominant_wind_direction"
        assert len(spatial["result"]["upwind_roads"]) > 0, "upwind_roads 为空"
        
        upwind_match = source.get("result", {}).get("component_scores", {}).get("upwind_match")
        assert upwind_match != 0.5, f"upwind_match 仍为默认值: {upwind_match}"
        
        print(f"  ✓ 通过:")
        print(f"    - Meteo dominant_wind_direction: {meteo['result']['dominant_wind_direction']}°")
        print(f"    - Spatial wind_direction_used: {spatial['meta']['wind_direction_used']}°")
        print(f"    - Spatial upwind_roads: {len(spatial['result']['upwind_roads'])} 条")
        print(f"    - Source upwind_match: {upwind_match}")


if __name__ == "__main__":
    print("=" * 70)
    print("风向契约修复测试")
    print("=" * 70)
    print()
    
    try:
        test_get_valid_wind_direction()
        test_spatial_agent_integration()
        test_coordinator_integration()
        
        print()
        print("=" * 70)
        print("✅ 所有测试通过！")
        print("=" * 70)
    except AssertionError as e:
        print()
        print("=" * 70)
        print(f"❌ 测试失败: {e}")
        print("=" * 70)
        sys.exit(1)
    except Exception as e:
        print()
        print("=" * 70)
        print(f"❌ 运行错误: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 70)
        sys.exit(1)
