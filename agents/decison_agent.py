# agents/decision_agent.py

import sys
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.desicion_rules import get_decision_by_score
from tools.decision_templates import DEFAULT_ROADS


def _extract_result(output):
    """安全提取result字段"""
    if not isinstance(output, dict):
        return {}
    result = output.get("result")
    return result if isinstance(result, dict) else {}


def run(source_result, spatial_result, traffic_result, meteo_result, user_input, air_result=None):
    """
    输入：上游Agent的输出 + 用户原始输入
    输出：标准JSON格式的建议结果
    
    参数：
        source_result: SourceAgent输出
        spatial_result: SpatialAgent输出
        traffic_result: TrafficAgent输出
        meteo_result: MeteorologyAgent输出
        user_input: 用户任务输入
        air_result: AirQualityAgent输出（可选，新增）
    """
    warnings = []
    created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    
    # 安全提取各Agent的result
    source_data = _extract_result(source_result)
    spatial_data = _extract_result(spatial_result)
    traffic_data = _extract_result(traffic_result)
    meteo_data = _extract_result(meteo_result)
    air_data = _extract_result(air_result) if air_result else {}
    
    # 1. 取风险分数（来自Source Agent）
    score = source_data.get("source_attribution", {}).get("traffic_source", 0.5)
    if score == 0.5:
        warnings.append("交通源可能性评分缺失，使用中性回退值0.5")
    
    # 2. 查规则表，得到建议等级和基础行动
    decision_info = get_decision_by_score(score)
    
    # 3. 取建议区域（来自Spatial Agent）
    area = spatial_data.get("affected_area", "站点周边1-3km缓冲区")
    
    # 4. 取建议道路（来自Spatial Agent，若为空则用默认备选）
    upwind_roads = spatial_data.get("upwind_roads", [])
    roads = []
    for r in upwind_roads:
        if isinstance(r, dict) and r.get("road_name"):
            roads.append(r["road_name"])
    
    # 只有上风向道路确实为空时才使用默认道路
    if not roads:
        station = user_input.get("station_name", "Mong Kok Roadside")
        roads = DEFAULT_ROADS.get(station, ["Main Road", "Second Road"])
        warnings.append(f"上风向道路列表为空，使用默认道路：{', '.join(roads[:3])}")
    
    # 5. 取重点车型（优先使用vehicle_focus）
    vehicles = (
        traffic_data.get("vehicle_focus")
        or traffic_data.get("dominant_vehicle_types")
        or ["Private Car", "Bus"]
    )
    
    # 6. 生成健康提醒（根据敏感点数量）
    poi_count = spatial_data.get("sensitive_poi_count", 0)
    actions = decision_info["actions"].copy()  # 复制基础行动
    if poi_count > 5:
        actions.append("周边学校医院密集，建议发布健康防护提醒。")
    else:
        actions.append("建议行人注意佩戴口罩。")
    
    # 7. 生成复盘指标（提取上游数值）
    indicators = []
    indicators.append(f"交通源可能性评分 = {score:.2f}")
    
    # AQHI必须从AirQualityAgent读取
    if air_data:
        aqhi_level = air_data.get("aqhi_level")
        avg_aqhi = air_data.get("avg_aqhi")
        if aqhi_level == "unknown" or avg_aqhi is None:
            indicators.append("AQHI数据不可用")
        else:
            indicators.append(f"AQHI等级 = {aqhi_level}")
    else:
        indicators.append("AQHI数据不可用")
    
    traffic_pressure_score = traffic_data.get("traffic_pressure_score")
    if traffic_pressure_score is not None:
        indicators.append(f"交通压力指数 = {traffic_pressure_score:.2f}")
    
    mean_wind_speed = meteo_data.get("mean_wind_speed")
    if mean_wind_speed is not None:
        indicators.append(f"平均风速 = {mean_wind_speed:.2f} m/s")
    else:
        indicators.append("平均风速 = 数据不可用")
    
    # 计算缺失率
    missing_components = 0
    total_components = 4
    if not source_data:
        missing_components += 1
    if not spatial_data:
        missing_components += 1
    if not traffic_data:
        missing_components += 1
    if not meteo_data:
        missing_components += 1
    missing_rate = missing_components / total_components
    
    # 计算置信度
    confidence = 0.80
    if missing_rate > 0:
        confidence -= 0.15 * missing_rate
    confidence = round(max(0.4, min(0.95, confidence)), 2)
    
    # 确定状态
    status = "success" if missing_rate == 0 else "warning"
   
    return {
        "agent_name": "DecisionAgent",
        "status": status,
        "result": {
            "decision_level": decision_info["level"],
            "suggested_area": area,
            "suggested_time": f"{user_input.get('start_time')} 至 {user_input.get('end_time')}",
            "suggested_roads": roads,
            "vehicle_focus": vehicles,
            "actions": actions,
            "review_indicators": indicators
        },
        "confidence": confidence,
        "evidence": ["基于污染溯源、交通结构和空间匹配综合判断。"],
        "data_quality": {
            "missing_rate": round(missing_rate, 2),
            "note": "；".join(warnings) if warnings else "输入数据完整"
        },
        "warnings": warnings,
        "created_at": created_at
    }


