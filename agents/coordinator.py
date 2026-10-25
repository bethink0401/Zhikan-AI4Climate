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
def match_station(selector: str, stations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Resolve a model-selected station identifier, never a free-form sentence."""
    def normalized(value: str) -> str:
        return re.sub(r"[\s\-]+", "_", value.strip().lower()).removesuffix("_roadside")

    selected = normalized(STATION_ALIAS_MAP.get(selector.lower().strip(), selector))
    candidates = [station for station in stations if selected in {
        normalized(str(station.get("station_id") or "")),
        normalized(str(station.get("station_name") or "")),
        normalized(str(station.get("station_name_raw") or "")),
    }]
    return max(candidates, key=lambda station: station.get("lat") is not None and
               station.get("lon") is not None) if candidates else None


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
        before = (context.station_name, context.start_time, context.end_time, context.lat, context.lon)
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

        if before != (context.station_name, context.start_time, context.end_time, context.lat, context.lon):
            context.agent_results.clear()
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
            result = meteorology_run(task, air_quality_result=context.agent_results.get("AirQualityAgent"),
                                     validation_db_path=context.db_path)
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


    def _build_agent(self):
        system_prompt = """你是城市污染分析平台的主控协调 Agent。
根据用户本轮的完整语义、否定、纠正和上下文自主选择工具，禁止按关键词命中决定业务。
历史只补充用户未修改的参数；用户明确改变地点或时段时先用 update_task_params 更新。
缺少必要参数时一次性询问，不得猜测站点、坐标、日期和业务事实。
一般天气出行建议不需要起终点，只有规划可通行路线才需要起终点。
使用已有结果可以回答时不要重复执行分析，也不要因用户提到某个词就调用工具。
工具能力：AirQualityAgent 查询空气质量，MeteorologyAgent 分析天气与传播，TrafficAgent 分析交通，
SpatialAgent 分析空间影响，SourceAgent 筛查来源，DecisionAgent 提供治理分析，ReportAgent 生成报告。
按工具的前置依赖调用：SpatialAgent 需要气象；SourceAgent 需要空气、气象、交通与空间；
DecisionAgent 需要来源；报告按用户要求和现有证据生成。模型决定目标，代码只执行和校验参数。
只使用工具真实返回的数据；模型不可用时不要用规则替代。"""

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

    async def chat(self, user_input: str) -> Dict[str, Any]:
        self.context.user_input = user_input

        enhanced_input = user_input
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

        enhanced_input = user_input
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
