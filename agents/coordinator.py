from __future__ import annotations
import asyncio
import json
import os
import sys
import re
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel, Field

from langchain_core.tools import StructuredTool
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.air_quality_agent import AirQualityAgent
from agents.traffic_agent import TrafficAgent
from agents.spatial_agent import SpatialAgent
from agents.source_agent import SourceAgent
from agents.report_agent import ReportAgent
from agents.meteorology_agent import run as meteorology_run
from agents.decison_agent import run as decision_run

load_dotenv()

# ==============================
# 【修改1】扩展别名映射，覆盖所有英文站名+Roadside后缀
# ==============================
STATION_ALIAS_MAP = {
    # 中文别名
    "旺角": "mong_kok",
    "观塘": "kwun_tong",
    "铜锣湾": "causeway_bay",
    "中环": "central",
    "尖沙咀": "tsim_sha_tsui",
    "屯门": "tuen_mun",
    "东涌": "tung_chung",
    "塔门": "tap_mun",
    "荃湾": "tsuen_wan",
    "深水埗": "sham_shui_po",
    "大埔": "tai_po",
    "元朗": "yuen_long",
    "北区": "north",
    "红磡": "hung_hom",
    # 补全：DB 中已有的其余 EPD 站（此前缺失导致口语提问匹配失败）
    "沙田": "shatin",
    "将军澳": "tseung_kwan_o",
    "南区": "southern",
    "葵涌": "kwai_chung",
    "中西区": "central_western",
    "东区": "eastern",
    # 英文基础名
    "mong kok": "mong_kok",
    "kwun tong": "kwun_tong",
    "causeway bay": "causeway_bay",
    "central": "central",
    "tsim sha tsui": "tsim_sha_tsui",
    "tuen mun": "tuen_mun",
    "tung chung": "tung_chung",
    "tap mun": "tap_mun",
    "tsuen wan": "tsuen_wan",
    "sham shui po": "sham_shui_po",
    "tai po": "tai_po",
    "yuen long": "yuen_long",
    "north": "north",
    "hung hom": "hung_hom",
    "shatin": "shatin",
    "tseung kwan o": "tseung_kwan_o",
    "southern": "southern",
    "kwai chung": "kwai_chung",
    "central western": "central_western",
    "eastern": "eastern",
    # 带Roadside后缀
    "mong kok roadside": "mong_kok",
    "kwun tong roadside": "kwun_tong",
    "causeway bay roadside": "causeway_bay",
    "central roadside": "central",
}

# ==============================
# 数据库站点加载函数
# 关键修复：DB里 Roadside 站点的 lat/lon 为 NULL，需从 station_mapping.json 合并补全
# ==============================
def _load_station_coords_mapping() -> Dict[str, Dict[str, float]]:
    """从 data/station_mapping.json 读取 Roadside 站点的真实经纬度。"""
    mapping_path = ROOT / "data" / "station_mapping.json"
    if not mapping_path.exists():
        return {}
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        coords: Dict[str, Dict[str, float]] = {}
        for station_name, info in (data.get("air_quality_to_atc") or {}).items():
            lat = info.get("lat")
            lon = info.get("lon")
            if lat is not None and lon is not None:
                coords[station_name] = {"lat": lat, "lon": lon}
        return coords
    except Exception:
        return {}


def load_stations_from_db(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT station_id, station_name, station_type, lat, lon, district FROM air_quality_stations")
        rows = cursor.fetchall()
        # 加载 station_mapping.json 用于补全 Roadside 站点缺失的 lat/lon
        coords_mapping = _load_station_coords_mapping()
        stations = []
        for row in rows:
            station_name = row[1]
            lat = row[3]
            lon = row[4]
            # DB 里 lat/lon 为 NULL 时，用 station_mapping.json 补全
            if (lat is None or lon is None) and station_name in coords_mapping:
                lat = coords_mapping[station_name]["lat"]
                lon = coords_mapping[station_name]["lon"]
            stations.append({
                "station_id": row[0].strip("_"),
                "station_name_raw": row[0],
                "station_name": station_name,
                "station_type": row[2],
                "lat": lat,
                "lon": lon,
                "district": row[5]
            })
        conn.close()
        return stations
    except Exception:
        return []

# ==============================
# 【修改2】重写站点匹配逻辑，彻底解决英文站名匹配问题
# 关键修复：同分时优先选择有 lat/lon 的站点，避免选到 None 坐标的 Roadside 站
# ==============================
def match_station(keyword: str, stations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not keyword or not stations:
        return None

    # 标准化关键词：小写、去空格、去后缀、统一分隔符
    keyword_norm = keyword.lower().strip()
    keyword_norm = re.sub(r'[\s\-]+', '_', keyword_norm)
    keyword_norm = keyword_norm.replace('_roadside', '').replace('roadside', '')

    # 收集所有达到阈值的候选站点
    candidates: List[tuple] = []

    for st in stations:
        sid = st["station_id"].lower()
        sname = st["station_name"].lower().replace(' ', '_')
        sname_norm = sname.replace('_roadside', '')

        score = 0
        # 完全匹配
        if keyword_norm == sid or keyword_norm == sname_norm:
            score = 100
        # 站点ID包含关键词
        elif keyword_norm in sid:
            score = 85
        # 站点名称包含关键词
        elif keyword_norm in sname_norm:
            score = 75
        # 关键词包含站点ID（反向包含，处理长文本匹配）
        elif sid in keyword_norm:
            score = 70

        if score >= 40:
            # 关键：是否有有效经纬度（Roadside 站点在DB里可能是None）
            has_coords = st.get("lat") is not None and st.get("lon") is not None
            # 元组排序：分数高优先，同分时有坐标优先
            candidates.append((score, has_coords, st))

    if not candidates:
        return None

    # 排序：先按分数降序，再按 has_coords 降序
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]

# ==============================
# 任务上下文数据结构（保留不变）
# ==============================
class TaskContext(BaseModel):
    task_id: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    user_input: str = ""
    # 当前轮任务类型，禁止被历史消息覆盖
    intent: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    station_id: Optional[str] = None
    station_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    db_path: Path = ROOT / "data" / "air_quality.db"
    agent_results: Dict[str, Any] = Field(default_factory=dict)
    _stations_cache: Optional[List[Dict[str, Any]]] = None

    @property
    def stations(self) -> List[Dict[str, Any]]:
        if self._stations_cache is None:
            self._stations_cache = load_stations_from_db(self.db_path)
        return self._stations_cache

# ==============================
# 工具参数 Schema（保留不变）
# ==============================
class TaskParamsInput(BaseModel):
    start_time: Optional[str] = Field(None, description="查询开始时间，格式：YYYY-MM-DD HH:MM:SS")
    end_time: Optional[str] = Field(None, description="查询结束时间，格式：YYYY-MM-DD HH:MM:SS")
    station_name: Optional[str] = Field(None, description="监测站点名称，支持中文、英文、拼音")
    lat: Optional[float] = Field(None, description="站点纬度")
    lon: Optional[float] = Field(None, description="站点经度")

class EmptyInput(BaseModel):
    pass

# ==============================
# 工具工厂
# ==============================
def create_tools(context: TaskContext) -> List[StructuredTool]:

    # ==============================
    # 【修改3】优化update_task_params返回信息，明确告诉模型成功/失败
    # ==============================
    def update_task_params(
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        station_name: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None
    ) -> str:
        result_lines = ["【参数更新结果】"]
        updated = False

        if start_time:
            context.start_time = start_time
            result_lines.append(f"已设置开始时间: {start_time}")
            updated = True
        if end_time:
            context.end_time = end_time
            result_lines.append(f"已设置结束时间: {end_time}")
            updated = True

        if station_name:
            station_info = match_station(station_name, context.stations)
            if station_info:
                context.station_id = station_info["station_name_raw"]
                context.station_name = station_info["station_name"]
                context.lat = station_info["lat"]
                context.lon = station_info["lon"]
                result_lines.append(f"已匹配站点: {station_info['station_name']} ({station_info['district']})")
                result_lines.append(f"经纬度: {station_info['lat']}, {station_info['lon']}")
                updated = True
            else:
                available = ", ".join([s["station_name"] for s in context.stations[:5]])
                result_lines.append(f"未找到站点: {station_name}")
                result_lines.append(f"可用站点示例: {available}")
        
        if lat:
            context.lat = lat
            result_lines.append(f"已设置纬度: {lat}")
            updated = True
        if lon:
            context.lon = lon
            result_lines.append(f"已设置经度: {lon}")
            updated = True

        if not updated:
            result_lines.append("未传入任何有效参数，上下文未变更")
        
        result_lines.append(f"当前时间范围: {context.start_time or '未设置'} ~ {context.end_time or '未设置'}")
        result_lines.append(f"当前站点: {context.station_name or '未设置'}")
        return "\n".join(result_lines)

    update_params_tool = StructuredTool(
        name="update_task_params",
        description="更新任务的站点、时间范围等参数。当用户提供了站点名、日期、时间段时必须调用此工具，必须传入提取到的station_name和start_time、end_time参数，禁止空参调用。",
        func=update_task_params,
        args_schema=TaskParamsInput
    )

    # 以下工具函数保持不变
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def air_quality_query() -> str:
        if not context.station_id or not context.start_time or not context.end_time:
            return "调用失败：缺少站点或时间参数，请先调用update_task_params补全。"

        # 关键修复：air_quality_hourly 表只有 general 站数据（如 CAUSEWAY BAY），
        # 没有 Roadside 站数据。查询前去掉 Roadside 后缀，否则 LIKE 匹配 0 行。
        query_station_name = re.sub(r'\s*Roadside\s*$', '', context.station_name, flags=re.IGNORECASE).strip()
        if not query_station_name:
            query_station_name = context.station_name

        print(f"\n[DEBUG] 空气质量查询 - 站点ID: {context.station_id}, 站点名: {context.station_name} -> 查询用: {query_station_name}")
        print(f"[DEBUG] 时间范围: {context.start_time} ~ {context.end_time}")

        try:
            agent = AirQualityAgent(db_path=context.db_path)
            task = {
                "task_id": context.task_id,
                "user_input": context.user_input,
                "start_time": context.start_time,
                "end_time": context.end_time,
                "station_id": context.station_id,
                "station_name": query_station_name,
                "lat": context.lat,
                "lon": context.lon
            }
            result = agent.run(task)
            context.agent_results["AirQualityAgent"] = result

            status = result.get("status")
            # 关键修复：子 agent 返回数据在 "result" 字段，不是 "data"
            data = result.get("result", {})

            if status == "failed":
                return f"空气质量查询失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            if status == "warning":
                # warning 表示该站点/时段无监测数据，不是系统错误
                note = result.get("data_quality", {}).get("note", "")
                return (
                    f"⚠️ 空气质量数据缺失\n"
                    f"- 站点：{context.station_name}\n"
                    f"- 时间：{context.start_time} ~ {context.end_time}\n"
                    f"- 说明：{note}\n"
                    f"该站点或时段暂无空气质量监测数据，建议尝试其他站点或时间。"
                )

            # success：返回完整数据
            main_pollutants = data.get("main_pollutants", [])
            pollutants_str = "、".join(main_pollutants) if main_pollutants else "无"
            return (
                f"✅ 空气质量查询成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 污染状态：{data.get('pollution_status', '未知')}\n"
                f"- 主要污染物：{pollutants_str}\n"
                f"- AQHI等级：{data.get('aqhi_level', '未知')}\n"
                f"- 交通污染信号：{data.get('traffic_pollutant_signal', '未知')}\n"
                f"- 异常评分：{data.get('anomaly_score', '未知')}"
            )
        except Exception as e:
            error_detail = f"{type(e).__name__}: {str(e)}"
            print(f"[DEBUG] 空气质量查询异常: {error_detail}")
            traceback.print_exc()
            return f"空气质量查询异常：{error_detail}"

    air_quality_tool = StructuredTool(
        name="AirQualityAgent",
        description="查询指定站点和时间段的空气质量数据，包括污染物浓度、AQHI等级。调用前必须确保站点和时间参数已设置。",
        func=air_quality_query,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def meteorology_query() -> str:
        if not context.station_name or not context.start_time or not context.end_time:
            return "调用失败：缺少站点或时间参数，请先调用update_task_params补全。"

        try:
            task = {
                "task_id": context.task_id,
                "user_input": context.user_input,
                "start_time": context.start_time,
                "end_time": context.end_time,
                "station_name": context.station_name,
                "lat": context.lat,
                "lon": context.lon
            }
            result = meteorology_run(task)
            context.agent_results["MeteorologyAgent"] = result

            if result.get("status") == "failed":
                return f"气象查询失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            # 关键修复：数据在 "result" 字段，字段名与子 agent 输出对齐
            data = result.get("result", {})
            return (
                f"✅ 气象条件查询成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 平均风速：{data.get('mean_wind_speed', '未知')} m/s\n"
                f"- 主导风向：{data.get('dominant_wind_direction_label', '未知')} ({data.get('dominant_wind_direction', '未知')}°)\n"
                f"- 扩散条件：{data.get('dispersion_condition', '未知')}\n"
                f"- 综合风险：{data.get('risk_type', '未知')} (score={data.get('weather_risk', '未知')})\n"
                f"- 平均湿度：{data.get('mean_humidity', '未知')}%\n"
                f"- 平均温度：{data.get('mean_temperature', '未知')}°C\n"
                f"- 累计降水：{data.get('total_precipitation', '未知')} mm"
            )
        except Exception as e:
            return f"气象查询异常：{type(e).__name__}: {str(e)}"

    meteorology_tool = StructuredTool(
        name="MeteorologyAgent",
        description="查询指定时间段的气象条件，包括风速、扩散条件、天气状况。调用前必须确保站点和时间参数已设置。",
        func=meteorology_query,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def traffic_query() -> str:
        if not context.station_name or not context.start_time or not context.end_time:
            return "调用失败：缺少站点或时间参数，请先调用update_task_params补全。"

        try:
            traffic_db = context.db_path.parent / "traffic.db"
            agent = TrafficAgent(db_path=traffic_db)
            task = {
                "task_id": context.task_id,
                "user_input": context.user_input,
                "start_time": context.start_time,
                "end_time": context.end_time,
                "station_name": context.station_name,
                "lat": context.lat,
                "lon": context.lon
            }
            result = agent.run(task)
            context.agent_results["TrafficAgent"] = result

            if result.get("status") == "failed":
                return f"交通污染分析失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            # 关键修复：数据在 "result" 字段，字段名与子 agent 输出对齐
            data = result.get("result", {})
            vehicle_types = data.get("dominant_vehicle_types", [])
            vehicle_str = "、".join(vehicle_types) if vehicle_types else "未知"
            return (
                f"✅ 交通污染分析成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 交通污染压力：{data.get('traffic_pressure', '未知')} (level={data.get('traffic_pressure_level', '未知')})\n"
                f"- 压力评分：{data.get('traffic_pressure_score', '未知')}\n"
                f"- 重型车辆信号：{data.get('heavy_vehicle_signal', '未知')}\n"
                f"- 主要车型：{vehicle_str}\n"
                f"- 公交占比：{data.get('bus_ratio', '未知')}\n"
                f"- 出租车占比：{data.get('taxi_ratio', '未知')}"
            )
        except Exception as e:
            return f"交通污染分析异常：{type(e).__name__}: {str(e)}"

    traffic_tool = StructuredTool(
        name="TrafficAgent",
        description="分析指定时间段的交通污染压力与车流量。调用前必须确保站点和时间参数已设置。",
        func=traffic_query,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def spatial_query() -> str:
        if not context.station_name or not context.lat or not context.lon:
            return "调用失败：缺少站点与经纬度参数，请先调用update_task_params补全。"
        if "MeteorologyAgent" not in context.agent_results:
            return "调用失败：缺少前置依赖，请先调用MeteorologyAgent获取气象数据。"

        try:
            agent = SpatialAgent()
            task = {
                "task_id": context.task_id,
                "user_input": context.user_input,
                "start_time": context.start_time,
                "end_time": context.end_time,
                "station_name": context.station_name,
                "lat": context.lat,
                "lon": context.lon
            }
            result = agent.run(task)
            context.agent_results["SpatialAgent"] = result

            if result.get("status") == "failed":
                return f"空间分析失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            # 关键修复：数据在 "result" 字段
            data = result.get("result", {})
            return (
                f"✅ 空间分布分析成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 分析结果：{json.dumps(data, ensure_ascii=False, indent=2)}"
            )
        except Exception as e:
            return f"空间分析异常：{type(e).__name__}: {str(e)}"

    spatial_tool = StructuredTool(
        name="SpatialAgent",
        description="分析污染空间影响范围与敏感点匹配。前置依赖：必须先调用MeteorologyAgent。",
        func=spatial_query,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def source_analysis() -> str:
        required = ["AirQualityAgent", "MeteorologyAgent", "TrafficAgent", "SpatialAgent"]
        missing = [name for name in required if name not in context.agent_results]
        if missing:
            return f"调用失败：缺少前置依赖 {missing}，请先执行上述基础工具。"

        try:
            agent = SourceAgent()
            result = agent.run(
                {"task_id": context.task_id, "user_input": context.user_input},
                context.agent_results["AirQualityAgent"],
                context.agent_results["MeteorologyAgent"],
                context.agent_results["TrafficAgent"],
                context.agent_results["SpatialAgent"]
            )
            context.agent_results["SourceAgent"] = result

            if result.get("status") == "failed":
                return f"污染源分析失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            # 关键修复：数据在 "result" 字段，字段名与 SourceAgent 实际输出对齐
            data = result.get("result", {})
            attribution = data.get("source_attribution", {})
            return (
                f"✅ 污染来源分析成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 主要判断：{data.get('main_judgement', '未知')}\n"
                f"- 风险等级：{data.get('risk_level', '未知')}\n"
                f"- 来源归因：\n"
                f"  · 交通源：{attribution.get('traffic_source', '未知')}\n"
                f"  · 局地积聚：{attribution.get('local_accumulation', '未知')}\n"
                f"  · 区域传输：{attribution.get('regional_transport', '未知')}\n"
                f"  · 扬尘源：{attribution.get('dust_source', '未知')}\n"
                f"  · 光化学源：{attribution.get('photochemical_source', '未知')}"
            )
        except Exception as e:
            return f"污染源分析异常：{type(e).__name__}: {str(e)}"

    source_tool = StructuredTool(
        name="SourceAgent",
        description="分析污染来源构成与各源贡献占比。前置依赖：空气质量、气象、交通、空间分析全部完成后调用。",
        func=source_analysis,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def decision_analysis() -> str:
        required = ["SourceAgent", "TrafficAgent", "SpatialAgent", "MeteorologyAgent"]
        missing = [name for name in required if name not in context.agent_results]
        if missing:
            return f"调用失败：缺少前置依赖 {missing}。"

        try:
            result = decision_run(
                context.agent_results["SourceAgent"],
                context.agent_results["SpatialAgent"],
                context.agent_results["TrafficAgent"],
                context.agent_results["MeteorologyAgent"],
                {
                    "task_id": context.task_id,
                    "user_input": context.user_input,
                    "start_time": context.start_time,
                    "end_time": context.end_time,
                    "station_name": context.station_name
                }
            )
            context.agent_results["DecisionAgent"] = result

            if result.get("status") == "failed":
                return f"决策建议生成失败：{result.get('data_quality', {}).get('note', '未知错误')}"

            # 关键修复：数据在 "result" 字段，字段名与 DecisionAgent 实际输出对齐
            data = result.get("result", {})
            actions = data.get("actions", [])
            actions_str = "\n  · ".join(actions) if actions else "无"
            return (
                f"✅ 治理决策建议生成成功\n"
                f"- 站点：{context.station_name}\n"
                f"- 决策等级：{data.get('decision_level', '未知')}\n"
                f"- 建议区域：{data.get('suggested_area', '未知')}\n"
                f"- 建议时段：{data.get('suggested_time', '未知')}\n"
                f"- 关注车型：{data.get('vehicle_focus', '未知')}\n"
                f"- 建议行动：\n  · {actions_str}"
            )
        except Exception as e:
            return f"决策建议生成异常：{type(e).__name__}: {str(e)}"

    decision_tool = StructuredTool(
        name="DecisionAgent",
        description="生成污染治理建议与应急响应等级。前置依赖：污染源、交通、空间、气象分析完成后调用。",
        func=decision_analysis,
        args_schema=EmptyInput
    )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3), retry=retry_if_exception_type(Exception))
    def report_generate() -> str:
        try:
            agent = ReportAgent()
            # 关键修复：ReportAgent 期望直接取 result 内部字段（如 main_pollutants），
            # 不是完整的 agent 返回结构。只传各 agent 的 "result" 字段。
            def _extract(agent_name):
                full = context.agent_results.get(agent_name, {})
                return full.get("result", {}) if isinstance(full, dict) else {}

            payload = {
                "air_quality": _extract("AirQualityAgent"),
                "meteorology": _extract("MeteorologyAgent"),
                "traffic": _extract("TrafficAgent"),
                "spatial": _extract("SpatialAgent"),
                "source": _extract("SourceAgent"),
                "decision": _extract("DecisionAgent"),
            }
            result = agent.run(payload)
            context.agent_results["ReportAgent"] = result

            # 关键修复：ReportAgent 没有 status 字段，数据在 "result" 字段
            data = result.get("result", {})
            return data.get("report_text", "报告生成完成")
        except Exception as e:
            return f"报告生成异常：{type(e).__name__}: {str(e)}"

    report_tool = StructuredTool(
        name="ReportAgent",
        description="生成完整的正式污染分析报告。建议所有分析完成后调用。",
        func=report_generate,
        args_schema=EmptyInput
    )

    return [
        update_params_tool,
        air_quality_tool,
        meteorology_tool,
        traffic_tool,
        spatial_tool,
        source_tool,
        decision_tool,
        report_tool
    ]

# ==============================
# 主控智能体核心类
# ==============================
class LangChainCoordinator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        db_path: Optional[Path] = None
    ):
        # 仅从环境变量读取，不在源码中保留任何密钥（LLM 为可选增强链路）
        self.api_key = api_key or os.getenv("COORDINATOR_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "未配置 COORDINATOR_API_KEY：LLM 调度链路不可用。"
                "请在环境变量中提供 OpenAI 兼容模型服务的密钥；"
                "确定性主链路与全部基准测试不依赖该配置。"
            )
        self.base_url = base_url or os.getenv("COORDINATOR_BASE_URL", "https://api.siliconflow.cn/v1")
        self.model = model or os.getenv("COORDINATOR_MODEL", "Qwen/Qwen3-32B")

        self.llm = ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=0.1,
            max_tokens=2000
        )

        self.context = TaskContext()
        if db_path:
            self.context.db_path = db_path

        self.tools = create_tools(self.context)
        self.chat_history = InMemoryChatMessageHistory()
        self._build_agent()


    # ==============================
    # 新增：当前轮意图识别
    # 历史只能补参数，不能决定Agent
    # ==============================
    def _detect_intent(self, user_input: str) -> Optional[str]:
        text = user_input.lower()

        meteorology_words = [
            "气象", "天气", "风速", "风向",
            "温度", "气温", "湿度", "风", "风速", "风向",
            "降雨", "降水", "云量", "气压", "扩散",
            "天气情况", "气候", "meteorology", "weather",
            "wind", "temperature", "humidity"
        ]

        air_words = [
            "空气质量", "空气", "aqi", "aqhi",
            "pm2.5", "pm10", "污染浓度", "污染物"
        ]

        traffic_words = [
            "交通", "车流", "车辆", "traffic"
        ]

        source_words = [
            "污染来源", "污染源", "来源", "来源分析",
            "source", "贡献", "归因", "源解析"
        ]

        decision_words = [
            "决策", "建议", "治理", "管控", "应对",
            "decision", "措施", "应急"
        ]

        report_words = [
            "报告", "report", "总结报告", "分析报告"
        ]

        if any(x in text for x in meteorology_words):
            return "meteorology"

        if any(x in text for x in air_words):
            return "air_quality"

        if any(x in text for x in traffic_words):
            return "traffic"

        if any(x in text for x in source_words):
            return "source"

        if any(x in text for x in decision_words):
            return "decision"

        if any(x in text for x in report_words):
            return "report"

        return None

    # ==============================
    # 新增：强制Agent路由层
    # 防止LLM默认调用错误Agent
    # ==============================
    def _route_agent(self, user_input: str) -> Optional[str]:
        intent = self._detect_intent(user_input)

        if intent == "meteorology":
            return "MeteorologyAgent"
        if intent == "air_quality":
            return "AirQualityAgent"
        if intent == "traffic":
            return "TrafficAgent"
        if intent == "source":
            return "SourceAgent"
        if intent == "decision":
            return "DecisionAgent"
        if intent == "report":
            return "ReportAgent"

        return None

    # ==============================
    # 依赖链定义：高级 agent 需要的前置 agent 列表（按执行顺序）
    # ==============================
    AGENT_DEPENDENCIES: Dict[str, List[str]] = {
        "SpatialAgent": ["MeteorologyAgent"],
        "SourceAgent": ["AirQualityAgent", "MeteorologyAgent", "TrafficAgent", "SpatialAgent"],
        "DecisionAgent": ["SourceAgent", "TrafficAgent", "SpatialAgent", "MeteorologyAgent"],
        "ReportAgent": [],  # ReportAgent 内部自行处理缺失数据，不强制依赖
    }

    def _run_dependency_chain(self, target_agent: str) -> List[str]:
        """
        按依赖顺序执行 target_agent 的所有前置依赖，最后执行 target_agent 本身。
        已有结果的前置 agent 会跳过。
        返回所有执行过的 agent 名称列表。
        """
        executed: List[str] = []
        deps = self.AGENT_DEPENDENCIES.get(target_agent, [])

        # 递归执行前置依赖
        for dep in deps:
            if dep not in self.context.agent_results:
                # 先递归执行该依赖的前置
                if dep in self.AGENT_DEPENDENCIES:
                    self._run_dependency_chain(dep)
                # 执行该依赖本身
                if self._params_ready_for(dep):
                    self._invoke_tool_directly(dep)
                    executed.append(dep)

        # 最后执行目标 agent
        if target_agent not in self.context.agent_results:
            self._invoke_tool_directly(target_agent)
            executed.append(target_agent)

        return executed


    # ==============================
    # 新增：上下文合并策略
    # 当前输入参数优先，缺失才继承历史
    # ==============================
    def _merge_request_context(self, user_input: str):

        new_intent = self._detect_intent(user_input)

        if new_intent and new_intent != getattr(self.context, "intent", None):
            # 任务类型变化，旧分析结果全部失效
            self.context.agent_results.clear()
            self.context.intent = new_intent
            # 清理历史工具调用影响，避免上一轮空气质量任务干扰当前气象任务
            self.chat_history.clear()

        old_station = self.context.station_name
        old_start = self.context.start_time

        self._auto_extract_params(user_input)

        # 地点或者时间变化，需要清理历史计算结果
        if (
            old_station != self.context.station_name
            or old_start != self.context.start_time
        ):
            self.context.agent_results.clear()


    # ==============================
    # 【修改4】重写自动参数提取，提取成功直接写入上下文并返回提示
    # ==============================
    def _auto_extract_params(self, user_input: str) -> str:
        """
        参数解析升级版：
        1. 当前输入地点优先
        2. 地点出现但无法匹配时清空旧地点，禁止污染
        3. 只有用户没有提供新地点时才继承历史地点
        """

        info_lines = []

        input_lower = user_input.lower()

        # ==========================
        # 时间解析
        # ==========================
        date_match = re.search(
            r'(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})[号日]?',
            user_input
        )

        time_match = re.search(
            r'(\d{1,2})[点时][到至\-~]*(\d{1,2})[点时]',
            user_input
        )

        if date_match:

            y, m, d = date_match.groups()

            date_str = f"{y}-{m.zfill(2)}-{d.zfill(2)}"

            if time_match:

                h1, h2 = time_match.groups()

                self.context.start_time = (
                    f"{date_str} {h1.zfill(2)}:00:00"
                )

                self.context.end_time = (
                    f"{date_str} {h2.zfill(2)}:00:00"
                )

            else:

                self.context.start_time = (
                    f"{date_str} 00:00:00"
                )

                self.context.end_time = (
                    f"{date_str} 23:59:59"
                )

            info_lines.append(
                f"自动识别时间: "
                f"{self.context.start_time} ~ "
                f"{self.context.end_time}"
            )


        # ==========================
        # 地点解析
        # ==========================

        old_station = self.context.station_name

        station_found = None

        # 判断用户是否明确输入地点关键词
        location_keyword_found = False


        for alias, key in STATION_ALIAS_MAP.items():

            if alias.lower() in input_lower:

                location_keyword_found = True

                station_found = match_station(
                    key,
                    self.context.stations
                )

                if station_found:
                    break


        # 用户直接输入完整站点名
        if not station_found:

            station_found = match_station(
                user_input,
                self.context.stations
            )


        if station_found:

            self.context.station_id = (
                station_found["station_name_raw"]
            )

            self.context.station_name = (
                station_found["station_name"]
            )

            self.context.lat = station_found["lat"]
            self.context.lon = station_found["lon"]


            info_lines.append(
                f"自动匹配站点: "
                f"{station_found['station_name']}"
                f" ({station_found['district']})"
            )


        elif location_keyword_found:

            # ==========================
            # 核心修复:
            # 用户明确输入新地点
            # 但是数据库没有匹配
            #
            # 不能继续使用旧地点
            # ==========================

            self.context.station_id = None
            self.context.station_name = None
            self.context.lat = None
            self.context.lon = None

            self.context.agent_results.clear()


            info_lines.append(
                "检测到新的地点输入，但是该地点未匹配到有效监测站点。"
                "已清除历史地点，禁止继续使用旧站点。"
            )


        # 地点变化清理旧结果
        if old_station != self.context.station_name:

            self.context.agent_results.clear()



        # 调试信息
        print(
            "[DEBUG PARAM UPDATE]",
            {
                "input": user_input,
                "station": self.context.station_name,
                "lat": self.context.lat,
                "lon": self.context.lon,
                "start": self.context.start_time,
                "end": self.context.end_time
            }
        )


        if info_lines:

            return (
                "【系统自动识别参数】\n"
                + "\n".join(info_lines)
                + "\n参数已更新，请严格使用当前参数。"
            )

        return ""

    # ==============================
    # 【修改5】强化系统提示词，明确禁止空参调用和重复反问
    # ==============================
    def _build_agent(self):
        system_prompt = """你是城市污染分析平台的主控协调Agent，严格遵守以下规则执行：

【意图识别规则（最高优先级）】
根据用户输入的关键词判定本轮意图，严格调用对应Agent，禁止默认调用AirQualityAgent：
- 含「气象/天气/风速/风向/温度/气温/湿度/降雨/降水/云量/气压/扩散/气候/weather/meteorology/wind/temperature/humidity」→ 调用 MeteorologyAgent
- 含「空气质量/AQI/AQHI/PM2.5/PM10/污染浓度/污染物」→ 调用 AirQualityAgent
- 含「交通/车流/车辆/traffic」→ 调用 TrafficAgent
- 含「报告/总结/report」→ 调用 ReportAgent
- 若用户未明确指定分析类型，再根据上下文判断；仍不明确时反问一次用户想分析哪类数据（气象/空气质量/交通）。

【系统路由结果服从规则】
若用户消息末尾出现【系统路由结果】段落，必须严格只调用该段落指定的Agent，禁止调用其他分析Agent。这是硬性约束，覆盖你自己的判断。

【当前轮优先规则】
1. 当前用户输入优先级最高，历史消息只能补充缺失参数。
2. 如果当前轮出现新的地点、时间、分析类型，必须覆盖历史状态。
3. 识别为气象意图时禁止调用AirQualityAgent；识别为空气质量意图时禁止调用MeteorologyAgent。

【核心执行流程】
1. 先检查用户输入和【系统自动识别参数】提示，确认站点名、时间范围是否已就绪。
2. 若参数已就绪（系统已自动识别），直接调用对应分析工具，不要再调用update_task_params。
3. 若参数缺失且系统未自动识别，调用update_task_params工具，必须把station_name、start_time、end_time作为参数传入，绝对禁止空参调用。
4. 参数齐全后，根据意图识别结果调用对应的分析工具，严格遵守前置依赖关系。
5. 基于工具返回的真实数据回答用户，禁止编造任何数据。

【参数规则】
- 站点支持中文、英文、带Roadside后缀、拼音，例如：旺角、Mong Kok、Mong Kok Roadside都可以识别。
- 时间支持绝对日期、时间段；用户说"整天/全天"默认当天00:00:00到23:59:59。
- 对话中已经确认过的参数永久继承，禁止重复询问。
- 如果用户输入中已经包含站点和时间，直接调用工具，禁止再反问确认。

【工具依赖关系】
- update_task_params：所有分析的前置，用于设置参数（仅当系统未自动识别参数时调用）
- AirQualityAgent / MeteorologyAgent / TrafficAgent：参数齐全即可调用，三者无依赖
- SpatialAgent：必须先调用MeteorologyAgent
- SourceAgent：必须先完成空气质量、气象、交通、空间四项分析
- DecisionAgent：必须先完成SourceAgent
- ReportAgent：所有分析完成后调用

【禁止行为红线】
1. 禁止在用户已提供站点和时间的情况下，继续反问用户要站点和时间
2. 禁止空参调用update_task_params工具
3. 禁止跳过前置依赖直接调用高级分析工具
4. 禁止不调用工具，凭空回答数据类问题
5. 禁止分多次、分轮次询问缺失参数，必须一次性问清
6. 禁止在用户询问气象时默认调用AirQualityAgent；禁止在用户询问空气质量时默认调用MeteorologyAgent

如果对话中出现【系统自动识别参数】的提示，说明参数已经设置完成，直接调用对应分析工具即可，不要再调用update_task_params，也不要再询问参数。"""

        agent_graph = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=system_prompt
        )

        self.agent_with_history = RunnableWithMessageHistory(
            runnable=agent_graph,
            get_session_history=lambda sid: self.chat_history,
            input_messages_key="messages",
            history_messages_key="messages"
        )

    # ==============================
    # 硬路由：意图明确时直接调用对应工具，不经过LLM工具决策
    # 解决LLM无视提示词、固定调用AirQualityAgent的问题
    # ==============================
    def _params_ready_for(self, agent_name: str) -> bool:
        """检查指定agent所需基础参数是否齐全（不含前置依赖检查，依赖链自动处理）"""
        has_station_id = bool(self.context.station_id)
        has_station_name = bool(self.context.station_name)
        has_time = bool(self.context.start_time and self.context.end_time)
        has_coords = (
            self.context.lat is not None
            and self.context.lon is not None
        )

        if agent_name == "AirQualityAgent":
            return has_station_id and has_time
        if agent_name in ("MeteorologyAgent", "TrafficAgent"):
            return has_station_name and has_time
        if agent_name == "SpatialAgent":
            return has_station_name and has_coords and has_time
        # 高级 agent 只需基础参数齐全，前置依赖由 _run_dependency_chain 自动执行
        if agent_name in ("SourceAgent", "DecisionAgent", "ReportAgent"):
            return has_station_name and has_time
        return False

    def _invoke_tool_directly(self, tool_name: str) -> str:
        """直接调用指定工具，不经过LLM agent"""
        tool_map = {t.name: t for t in self.tools}
        tool = tool_map.get(tool_name)
        if not tool:
            return f"内部错误：未找到工具 {tool_name}"
        try:
            return tool.invoke({})
        except Exception as e:
            return f"工具执行异常: {type(e).__name__}: {str(e)}"

    async def _stream_format_answer(
        self, user_input: str, tool_name: str, tool_result: str
    ) -> AsyncGenerator[str, None]:
        """用LLM把工具结果流式格式化为自然语言回答"""
        prompt = (
            f"用户问题: {user_input}\n\n"
            f"已调用工具 {tool_name}，返回结果:\n{tool_result}\n\n"
            f"请基于上述工具返回的真实数据，用简洁的自然语言回答用户问题。"
            f"要求：直接给出分析结论，禁止编造数据，禁止反问用户。"
        )
        async for chunk in self.llm.astream([HumanMessage(content=prompt)]):
            token = chunk.content
            if token and isinstance(token, str):
                yield token

    def _build_route_fallback_input(
        self, user_input: str, auto_info: str, route_agent: Optional[str]
    ) -> str:
        """参数不齐时，构造LLM反问用的增强输入"""
        route_info = ""
        if route_agent:
            route_info = (
                f"\n\n【系统路由结果】\n"
                f"当前用户请求必须调用: {route_agent}\n"
                f"禁止调用其他分析Agent。\n"
                f"请先确保参数完整，然后直接执行对应工具。\n"
            )
        if auto_info:
            return f"{user_input}\n\n{auto_info}{route_info}"
        return f"{user_input}{route_info}"

    def _is_advanced_agent(self, agent_name: str) -> bool:
        """判断是否为需要依赖链执行的高级 agent"""
        return agent_name in ("SourceAgent", "DecisionAgent", "ReportAgent")

    async def chat(self, user_input: str) -> Dict[str, Any]:
        self.context.user_input = user_input

        # 上下文合并：意图变化时清空历史，避免上一轮工具调用干扰当前决策
        self._merge_request_context(user_input)

        # 自动提取参数
        auto_info = self._auto_extract_params(user_input)

        # 硬路由：意图明确且参数齐全时，直接调用对应工具，不经过LLM工具决策
        route_agent = self._route_agent(user_input)
        if route_agent:
            print(f"[ROUTER] 硬路由选择: {route_agent}")
            if self._params_ready_for(route_agent):
                if self._is_advanced_agent(route_agent):
                    # 高级 agent：执行完整依赖链
                    print(f"[DEPENDENCY] 执行依赖链: {route_agent}")
                    self._run_dependency_chain(route_agent)
                    # 汇总目标 agent 的结果
                    tool_result = self._format_agent_result(route_agent)
                else:
                    # 基础 agent：直接调用
                    tool_result = self._invoke_tool_directly(route_agent)

                # 用LLM格式化自然语言回答
                answer_tokens: List[str] = []
                async for token in self._stream_format_answer(
                    user_input, route_agent, tool_result
                ):
                    answer_tokens.append(token)
                final_answer = "".join(answer_tokens)
                return {
                    "answer": final_answer,
                    "context": self.context.model_dump(),
                    "agent_results": self.context.agent_results
                }
            # 参数不齐，走LLM反问（fall through）

        # LLM agent 流程（参数不齐或无明确路由时）
        enhanced_input = self._build_route_fallback_input(
            user_input, auto_info, route_agent
        )
        result = await self.agent_with_history.ainvoke(
            {"messages": [HumanMessage(content=enhanced_input)]},
            config={"configurable": {"session_id": "default"}}
        )

        final_answer = result["messages"][-1].content
        return {
            "answer": final_answer,
            "context": self.context.model_dump(),
            "agent_results": self.context.agent_results
        }

    def _format_agent_result(self, agent_name: str) -> str:
        """从 context.agent_results 提取指定 agent 的结果摘要"""
        result = self.context.agent_results.get(agent_name, {})
        if not result:
            return f"{agent_name} 无结果"

        data = result.get("result", {})
        status = result.get("status", "unknown")

        if agent_name == "SourceAgent":
            attribution = data.get("source_attribution", {})
            return (
                f"✅ 污染来源分析完成 (status={status})\n"
                f"- 主要判断：{data.get('main_judgement', '未知')}\n"
                f"- 风险等级：{data.get('risk_level', '未知')}\n"
                f"- 交通源贡献：{attribution.get('traffic_source', '未知')}\n"
                f"- 区域传输：{attribution.get('regional_transport', '未知')}\n"
                f"- 局地积聚：{attribution.get('local_accumulation', '未知')}"
            )
        if agent_name == "DecisionAgent":
            actions = data.get("actions", [])
            actions_str = "、".join(actions) if actions else "无"
            return (
                f"✅ 决策建议生成完成 (status={status})\n"
                f"- 决策等级：{data.get('decision_level', '未知')}\n"
                f"- 建议区域：{data.get('suggested_area', '未知')}\n"
                f"- 关注车型：{data.get('vehicle_focus', '未知')}\n"
                f"- 建议行动：{actions_str}"
            )
        if agent_name == "ReportAgent":
            return data.get("report_text", "报告生成完成")
        return json.dumps(data, ensure_ascii=False, indent=2)

    async def stream_chat(self, user_input: str) -> AsyncGenerator[Dict[str, Any], None]:
        self.context.user_input = user_input

        # 上下文合并：意图变化时清空历史
        self._merge_request_context(user_input)

        auto_info = self._auto_extract_params(user_input)

        # 硬路由：意图明确且参数齐全时，直接调用工具
        route_agent = self._route_agent(user_input)
        if route_agent:
            print(f"[ROUTER] 硬路由选择: {route_agent}")
            if self._params_ready_for(route_agent):
                if self._is_advanced_agent(route_agent):
                    # 高级 agent：执行完整依赖链，流式输出每个 agent 状态
                    print(f"[DEPENDENCY] 执行依赖链: {route_agent}")
                    deps = self.AGENT_DEPENDENCIES.get(route_agent, [])
                    for dep in deps:
                        if dep not in self.context.agent_results:
                            if dep in self.AGENT_DEPENDENCIES:
                                # 递归执行子依赖
                                for sub_dep in self.AGENT_DEPENDENCIES.get(dep, []):
                                    if sub_dep not in self.context.agent_results and self._params_ready_for(sub_dep):
                                        ts = datetime.now(timezone.utc).astimezone().isoformat()
                                        yield {"type": "tool_start", "tool": sub_dep, "timestamp": ts}
                                        self._invoke_tool_directly(sub_dep)
                                        yield {"type": "tool_end", "tool": sub_dep, "output": "", "timestamp": datetime.now(timezone.utc).astimezone().isoformat()}
                            if self._params_ready_for(dep):
                                ts = datetime.now(timezone.utc).astimezone().isoformat()
                                yield {"type": "tool_start", "tool": dep, "timestamp": ts}
                                self._invoke_tool_directly(dep)
                                yield {"type": "tool_end", "tool": dep, "output": "", "timestamp": datetime.now(timezone.utc).astimezone().isoformat()}

                    # 最后执行目标 agent
                    ts = datetime.now(timezone.utc).astimezone().isoformat()
                    yield {"type": "tool_start", "tool": route_agent, "timestamp": ts}
                    self._invoke_tool_directly(route_agent)
                    tool_result = self._format_agent_result(route_agent)
                    yield {"type": "tool_end", "tool": route_agent, "output": tool_result, "timestamp": datetime.now(timezone.utc).astimezone().isoformat()}
                else:
                    # 基础 agent：直接调用
                    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
                    yield {"type": "tool_start", "tool": route_agent, "timestamp": timestamp}
                    tool_result = self._invoke_tool_directly(route_agent)
                    yield {"type": "tool_end", "tool": route_agent, "output": tool_result, "timestamp": datetime.now(timezone.utc).astimezone().isoformat()}

                # 用LLM流式格式化回答
                async for token in self._stream_format_answer(
                    user_input, route_agent, tool_result
                ):
                    yield {
                        "type": "thinking",
                        "content": token,
                        "timestamp": datetime.now(timezone.utc).astimezone().isoformat()
                    }

                # 最终结果
                yield {
                    "type": "final",
                    "answer": self.context.agent_results,
                    "context": self.context.model_dump(),
                    "timestamp": datetime.now(timezone.utc).astimezone().isoformat()
                }
                return
            # 参数不齐，走LLM反问（fall through）

        # LLM agent 流程（参数不齐或无明确路由时）
        enhanced_input = self._build_route_fallback_input(
            user_input, auto_info, route_agent
        )

        async for event in self.agent_with_history.astream_events(
            {"messages": [HumanMessage(content=enhanced_input)]},
            config={"configurable": {"session_id": "default"}},
            version="v1"
        ):
            event_type = event["event"]
            timestamp = datetime.now(timezone.utc).astimezone().isoformat()

            if event_type == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token and isinstance(token, str):
                    yield {
                        "type": "thinking",
                        "content": token,
                        "timestamp": timestamp
                    }

            elif event_type == "on_tool_start":
                tool_name = event.get("name", "unknown_tool")
                yield {
                    "type": "tool_start",
                    "tool": tool_name,
                    "timestamp": timestamp
                }

            elif event_type == "on_tool_end":
                tool_name = event.get("name", "unknown_tool")
                output = event["data"].get("output", "")
                yield {
                    "type": "tool_end",
                    "tool": tool_name,
                    "output": output,
                    "timestamp": timestamp
                }

            elif event_type == "on_chain_end" and event.get("name") == "LangGraph":
                yield {
                    "type": "final",
                    "answer": self.context.agent_results,
                    "context": self.context.model_dump(),
                    "timestamp": timestamp
                }

# ==============================
# 终端交互入口（保留不变）
# ==============================
async def run_cli():
    print("=" * 60)
    print("城市污染分析智能体 - 终端交互模式")
    print("输入 exit 退出 | 输入 clear 重置对话")
    print("=" * 60)
    
    coordinator = LangChainCoordinator()
    
    while True:
        try:
            user_input = input("\n你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("再见！")
                break
            if user_input.lower() == "clear":
                coordinator = LangChainCoordinator()
                print("✅ 对话已重置")
                continue
            
            print("\n智能体: ", end="", flush=True)
            async for chunk in coordinator.stream_chat(user_input):
                if chunk["type"] == "thinking":
                    print(chunk["content"], end="", flush=True)
                elif chunk["type"] == "tool_start":
                    print(f"\n\n[调用工具: {chunk['tool']}]", end="", flush=True)
                elif chunk["type"] == "tool_end":
                    print(f"\n[工具 {chunk['tool']} 执行完成]", end="", flush=True)
            print()
        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"\n❌ 发生错误: {str(e)}")
            traceback.print_exc()

# ==============================
# FastAPI服务入口（保留不变）
# ==============================
app = FastAPI(title="城市污染分析智能体 API")
sessions: Dict[str, LangChainCoordinator] = {}

@app.post("/api/chat")
async def chat_endpoint(session_id: str, user_input: str):
    if session_id not in sessions:
        sessions[session_id] = LangChainCoordinator()
    coordinator = sessions[session_id]
    return await coordinator.chat(user_input)

@app.get("/api/chat/stream")
async def chat_stream_endpoint(session_id: str, user_input: str):
    if session_id not in sessions:
        sessions[session_id] = LangChainCoordinator()
    coordinator = sessions[session_id]
    
    async def event_generator():
        async for chunk in coordinator.stream_chat(user_input):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ==============================
# 主函数（保留不变）
# ==============================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="LangChain 多智能体协调器")
    parser.add_argument("--mode", choices=["cli", "server"], default="cli", help="运行模式：cli 终端交互 | server 启动API服务")
    parser.add_argument("--port", type=int, default=8000, help="API 服务端口，仅 server 模式生效")
    args = parser.parse_args()
    
    if args.mode == "cli":
        asyncio.run(run_cli())
    elif args.mode == "server":
        uvicorn.run(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()