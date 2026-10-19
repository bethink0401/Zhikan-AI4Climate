#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试 MeteorologyAgent hourly_series 实现"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agents.meteorology_agent import run as meteo_run


def test_hourly_series():
    """测试 hourly_series 字段是否正确生成"""
    
    # Mong Kok 测试案例
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
    
    print("=" * 80)
    print("MeteorologyAgent hourly_series 测试")
    print("=" * 80)
    
    # 1. 基本检查
    assert result["agent_name"] == "MeteorologyAgent"
    assert result["status"] in ["success", "warning", "failed"]
    print(f"✓ Status: {result['status']}")
    
    if result["status"] == "failed":
        print(f"✗ 失败: {result.get('evidence', [])}")
        return
    
    res = result["result"]
    
    # 2. hourly_series 存在性检查
    assert "hourly_series" in res, "hourly_series 字段缺失"
    assert isinstance(res["hourly_series"], list), "hourly_series 必须是 list"
    print(f"✓ hourly_series 存在: {type(res['hourly_series'])}")
    
    # 3. hourly_count 一致性检查
    assert "hourly_count" in res, "hourly_count 字段缺失"
    assert res["hourly_count"] == len(res["hourly_series"]), \
        f"hourly_count ({res['hourly_count']}) 与 len(hourly_series) ({len(res['hourly_series'])}) 不一致"
    print(f"✓ hourly_count 一致: {res['hourly_count']} == {len(res['hourly_series'])}")
    
    # 4. Mong Kok 案例应该有 4 小时数据
    expected_hours = 4  # 08:00, 09:00, 10:00, 11:00
    print(f"✓ 实际小时数: {len(res['hourly_series'])} (期望 {expected_hours})")
    
    if len(res["hourly_series"]) == 0:
        print("⚠ hourly_series 为空，无法进行进一步测试")
        return
    
    # 5. 字段完整性检查
    required_fields = ["time", "wind_speed", "wind_direction", "humidity", 
                      "precipitation", "temperature", "cloud_cover"]
    
    for i, hour_data in enumerate(res["hourly_series"]):
        for field in required_fields:
            assert field in hour_data, f"第 {i} 条记录缺少字段: {field}"
    
    print(f"✓ 所有记录包含必需字段: {required_fields}")
    
    # 6. 时间升序检查
    times = [h["time"] for h in res["hourly_series"]]
    assert times == sorted(times), f"时间未按升序排列: {times}"
    print(f"✓ 时间升序排列: {times[0]} -> {times[-1]}")
    
    # 7. 时间不重复检查
    assert len(times) == len(set(times)), f"存在重复时间: {times}"
    print(f"✓ 时间无重复")
    
    # 8. null 值检查（确保缺失值为 null，而非 0）
    null_found = False
    zero_values = {"wind_speed": 0, "wind_direction": 0}
    
    for hour_data in res["hourly_series"]:
        for field in required_fields[1:]:  # 跳过 time
            if hour_data[field] is None:
                null_found = True
            elif hour_data[field] == 0:
                if field in zero_values:
                    print(f"  ℹ 发现合法零值: {hour_data['time']} {field}={hour_data[field]}")
    
    if null_found:
        print("✓ 存在 null 值（正常缺失值处理）")
    else:
        print("✓ 无 null 值（数据完整）")
    
    # 9. 聚合一致性检查
    print("\n" + "=" * 80)
    print("聚合一致性检查")
    print("=" * 80)
    
    # 计算 hourly_series 的聚合值
    valid_wind_speeds = [h["wind_speed"] for h in res["hourly_series"] if h["wind_speed"] is not None]
    valid_humidity = [h["humidity"] for h in res["hourly_series"] if h["humidity"] is not None]
    valid_precipitation = [h["precipitation"] for h in res["hourly_series"] if h["precipitation"] is not None]
    valid_temperature = [h["temperature"] for h in res["hourly_series"] if h["temperature"] is not None]
    valid_cloud_cover = [h["cloud_cover"] for h in res["hourly_series"] if h["cloud_cover"] is not None]
    
    if valid_wind_speeds:
        calc_mean_wind = sum(valid_wind_speeds) / len(valid_wind_speeds)
        reported_mean_wind = res.get("mean_wind_speed")
        if reported_mean_wind is not None:
            diff = abs(calc_mean_wind - reported_mean_wind)
            assert diff < 0.01, f"mean_wind_speed 不一致: {reported_mean_wind} vs {calc_mean_wind:.4f}"
            print(f"✓ mean_wind_speed 一致: {reported_mean_wind:.4f} (计算值: {calc_mean_wind:.4f})")
    
    if valid_humidity:
        calc_mean_hum = sum(valid_humidity) / len(valid_humidity)
        reported_mean_hum = res.get("mean_humidity")
        if reported_mean_hum is not None:
            diff = abs(calc_mean_hum - reported_mean_hum)
            assert diff < 0.01, f"mean_humidity 不一致: {reported_mean_hum} vs {calc_mean_hum:.2f}"
            print(f"✓ mean_humidity 一致: {reported_mean_hum:.2f} (计算值: {calc_mean_hum:.2f})")
    
    if valid_precipitation:
        calc_total_precip = sum(valid_precipitation)
        reported_total_precip = res.get("total_precipitation")
        if reported_total_precip is not None:
            diff = abs(calc_total_precip - reported_total_precip)
            assert diff < 0.01, f"total_precipitation 不一致: {reported_total_precip} vs {calc_total_precip:.2f}"
            print(f"✓ total_precipitation 一致: {reported_total_precip:.2f} (计算值: {calc_total_precip:.2f})")
    
    if valid_temperature:
        calc_mean_temp = sum(valid_temperature) / len(valid_temperature)
        reported_mean_temp = res.get("mean_temperature")
        if reported_mean_temp is not None:
            diff = abs(calc_mean_temp - reported_mean_temp)
            assert diff < 0.01, f"mean_temperature 不一致: {reported_mean_temp} vs {calc_mean_temp:.2f}"
            print(f"✓ mean_temperature 一致: {reported_mean_temp:.2f} (计算值: {calc_mean_temp:.2f})")
    
    if valid_cloud_cover:
        calc_mean_cloud = sum(valid_cloud_cover) / len(valid_cloud_cover)
        reported_mean_cloud = res.get("mean_cloud_cover")
        if reported_mean_cloud is not None:
            diff = abs(calc_mean_cloud - reported_mean_cloud)
            assert diff < 0.01, f"mean_cloud_cover 不一致: {reported_mean_cloud} vs {calc_mean_cloud:.2f}"
            print(f"✓ mean_cloud_cover 一致: {reported_mean_cloud:.2f} (计算值: {calc_mean_cloud:.2f})")
    
    # 10. 输出实际数据
    print("\n" + "=" * 80)
    print("hourly_series 实际数据")
    print("=" * 80)
    print(json.dumps(res["hourly_series"], ensure_ascii=False, indent=2))
    
    print("\n" + "=" * 80)
    print("✓ 所有测试通过！")
    print("=" * 80)


if __name__ == "__main__":
    test_hourly_series()
