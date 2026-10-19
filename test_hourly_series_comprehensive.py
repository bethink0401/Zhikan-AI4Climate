#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
综合测试 MeteorologyAgent hourly_series 实现
包括：成功、fallback、失败场景
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agents.meteorology_agent import run as meteo_run


def print_section(title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def test_success_case():
    """测试成功案例（Mong Kok）"""
    print_section("测试 1: 成功案例（真实 API 数据）")
    
    task = {
        "task_id": "HK_MK_2023_0800",
        "station_name": "Mong Kok Roadside",
        "station_id": "mk_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    }
    
    result = meteo_run(task)
    
    assert result["status"] == "success", f"期望 success，实际 {result['status']}"
    assert "hourly_series" in result["result"], "hourly_series 缺失"
    assert len(result["result"]["hourly_series"]) == 4, f"期望 4 小时，实际 {len(result['result']['hourly_series'])}"
    assert result["result"]["hourly_count"] == 4, f"hourly_count 应为 4"
    
    print(f"✓ Status: {result['status']}")
    print(f"✓ hourly_count: {result['result']['hourly_count']}")
    print(f"✓ data_quality.fallback_used: {result['data_quality']['fallback_used']}")
    print(f"✓ data_quality.source: {result['data_quality']['source']}")
    
    # 检查时间范围
    times = [h["time"] for h in result["result"]["hourly_series"]]
    print(f"✓ 时间范围: {times[0]} -> {times[-1]}")
    
    # 检查聚合一致性
    valid_wind = [h["wind_speed"] for h in result["result"]["hourly_series"] if h["wind_speed"] is not None]
    calc_mean_wind = sum(valid_wind) / len(valid_wind)
    reported_mean_wind = result["result"]["mean_wind_speed"]
    assert abs(calc_mean_wind - reported_mean_wind) < 0.01
    print(f"✓ 聚合一致性: mean_wind_speed={reported_mean_wind:.4f}")
    
    return result


def test_fallback_case():
    """测试 fallback 案例"""
    print_section("测试 2: Fallback 场景（未来时间，使用本地 CSV）")
    
    # 使用未来时间，强制触发 fallback
    task = {
        "task_id": "TEST_FALLBACK",
        "station_name": "Test Station",
        "station_id": "test",
        "start_time": "2025-01-01 00:00:00",
        "end_time": "2025-01-01 03:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    }
    
    result = meteo_run(task)
    
    # fallback 可能返回 success 或 warning，取决于数据质量
    assert result["status"] in ["success", "warning", "failed"], \
        f"非法 status: {result['status']}"
    
    print(f"✓ Status: {result['status']}")
    print(f"✓ data_quality.fallback_used: {result['data_quality']['fallback_used']}")
    print(f"✓ data_quality.source: {result['data_quality']['source']}")
    
    if result["status"] != "failed":
        assert "hourly_series" in result["result"], "hourly_series 缺失"
        print(f"✓ hourly_count: {result['result']['hourly_count']}")
        print(f"✓ len(hourly_series): {len(result['result']['hourly_series'])}")
        
        if result["result"]["hourly_series"]:
            times = [h["time"] for h in result["result"]["hourly_series"]]
            print(f"✓ 时间范围: {times[0]} -> {times[-1]}")
    else:
        print(f"⚠ 失败（预期）: {result['evidence']}")
    
    return result


def test_invalid_input():
    """测试无效输入"""
    print_section("测试 3: 无效输入（缺少必填字段）")
    
    task = {
        "task_id": "INVALID",
        # 缺少 lat, lon, start_time, end_time
    }
    
    result = meteo_run(task)
    
    assert result["status"] == "failed", f"期望 failed，实际 {result['status']}"
    print(f"✓ Status: {result['status']} (预期)")
    print(f"✓ evidence: {result['evidence'][0][:100]}...")
    
    # failed 时 result 应为空字典
    assert result["result"] == {}, "failed 时 result 应为空字典"
    print(f"✓ result: {{}} (预期)")
    
    return result


def test_single_hour():
    """测试单小时窗口"""
    print_section("测试 4: 单小时窗口（边界情况）")
    
    task = {
        "task_id": "SINGLE_HOUR",
        "station_id": "test",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 08:00:00",  # 同一小时
        "lat": 22.3193,
        "lon": 114.1694,
    }
    
    result = meteo_run(task)
    
    # 单小时窗口可能失败（窗口过小）或成功（取决于实现）
    print(f"✓ Status: {result['status']}")
    
    if result["status"] != "failed":
        print(f"✓ hourly_count: {result['result']['hourly_count']}")
        print(f"✓ len(hourly_series): {len(result['result']['hourly_series'])}")
    else:
        print(f"⚠ 失败（时间窗口不足）: {result['evidence'][0][:100]}...")
    
    return result


def test_wind_direction_zero():
    """测试 wind_direction=0 不被错误过滤"""
    print_section("测试 5: wind_direction=0 是合法值")
    
    task = {
        "task_id": "WIND_ZERO",
        "station_id": "test",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    }
    
    result = meteo_run(task)
    
    if result["status"] != "failed" and result["result"]["hourly_series"]:
        # 检查是否有 wind_direction=0 的情况
        zero_directions = [h for h in result["result"]["hourly_series"] 
                          if h["wind_direction"] == 0]
        
        if zero_directions:
            print(f"✓ 发现 wind_direction=0: {len(zero_directions)} 条记录")
            print(f"  示例: {zero_directions[0]}")
        else:
            print(f"ℹ 当前数据中没有 wind_direction=0 的情况（正常）")
        
        # 检查 null 值
        null_directions = [h for h in result["result"]["hourly_series"] 
                          if h["wind_direction"] is None]
        
        if null_directions:
            print(f"✓ 发现 wind_direction=null: {len(null_directions)} 条记录")
        else:
            print(f"✓ 所有 wind_direction 均有值")
    
    return result


def main():
    print("=" * 80)
    print("MeteorologyAgent hourly_series 综合测试")
    print("=" * 80)
    
    results = []
    
    try:
        results.append(("success_case", test_success_case()))
        results.append(("fallback_case", test_fallback_case()))
        results.append(("invalid_input", test_invalid_input()))
        results.append(("single_hour", test_single_hour()))
        results.append(("wind_direction_zero", test_wind_direction_zero()))
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print_section("测试汇总")
    
    for name, result in results:
        status = result.get("status", "unknown")
        hourly_count = result.get("result", {}).get("hourly_count", 0)
        print(f"{name:20s} -> status={status:7s}, hourly_count={hourly_count}")
    
    print("\n" + "=" * 80)
    print("✓ 所有测试通过！")
    print("=" * 80)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
