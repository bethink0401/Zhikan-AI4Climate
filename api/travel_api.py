# -*- coding: utf-8 -*-
"""TravelAgent 的 HTTP 服务层。

路由只负责：解析请求 → 调用 Agent → 包装响应。
出行条件换算、路线比选与建议生成全部在 agents/ 与 tools/ 中，本文件不重复实现。
输入校验统一由 agents.travel_agent.validate_task 完成。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from flask import Blueprint, Flask, Response, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.air_quality_agent import AirQualityAgent  # noqa: E402
from agents.traffic_agent import TrafficAgent  # noqa: E402
from agents.travel_agent import TravelAgent, validate_task  # noqa: E402
from api.stations import epd_stations  # noqa: E402
from tools.travel_advice_tools import list_profiles, load_travel_config  # noqa: E402

# 案例演示的完整输出，由 examples/run_travel_case.py 生成。
# 允许用环境变量指到别处，便于换一份案例跑演示。
DEFAULT_CASE_OUTPUT = ROOT / "examples" / "travel_case_output.json"

#: 供 /plan 现场取真实数据的 Agent 实例（构造轻，查询走本地 SQLite）。
_air_agent = AirQualityAgent()
_traffic_agent = TrafficAgent()


#: Open-Meteo 实时取数在进程内做一份小缓存：同一站点同一时段反复规划时
#: （改起终点不改时间的场景最常见）直接命中，省掉每次十几秒的网络等待。
#: 键取 站点+坐标+时间窗，容量超限时淘汰最早的条目。
_METEO_CACHE: dict[str, dict[str, Any]] = {}
_METEO_CACHE_ORDER: list[str] = []
_METEO_CACHE_LIMIT = 32
_METEO_CACHE_LOCK = threading.Lock()


def _combined_meteorology(task: dict[str, Any]) -> dict[str, Any] | None:
    """对全部路边站**分别**取气象，再合成一份气象输入。

    Open-Meteo 是网格数据，任意坐标都能查 —— 所谓"全部气象站点"在这里
    落地为：每个污染源站各自取一份该时段的逐小时气象，然后
    风按矢量平均（避免 350° 与 10° 平均成 180° 的错误）、
    温度 / 湿度 / 降水 / 各风险分按算术平均、逐小时序列按时间戳对齐平均。
    合成后的结构仍是 Meteorology Agent 的响应格式，travel_agent 无需改动。

    全部失败时返回 None，让 travel_agent 退回按起点取气象的旧行为。
    """
    import math
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone

    from agents.meteorology_agent import run as meteorology_run
    from tools.meteorology_tools import wind_direction_label

    stations = epd_stations()
    if not stations:
        return None

    created = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def cache_key(station: dict[str, Any]) -> str:
        return (
            f"{station['station_id']}|{round(station['lat'], 4)}|{round(station['lon'], 4)}"
            f"|{task.get('start_time')}|{task.get('end_time')}"
        )

    def fetch(station: dict[str, Any]) -> dict[str, Any]:
        key = cache_key(station)
        with _METEO_CACHE_LOCK:
            cached = _METEO_CACHE.get(key)
            if cached is not None:
                return cached
        run = meteorology_run(
            {
                "task_id": f"{task.get('task_id')}_meteo_{station['station_id']}",
                "station_id": station["station_id"],
                "station_name": station["station_name"],
                "lat": station["lat"],
                "lon": station["lon"],
                "start_time": task.get("start_time"),
                "end_time": task.get("end_time"),
            }
        )
        with _METEO_CACHE_LOCK:
            if run.get("status") in ("success", "warning") and key not in _METEO_CACHE:
                _METEO_CACHE[key] = run
                _METEO_CACHE_ORDER.append(key)
                while len(_METEO_CACHE_ORDER) > _METEO_CACHE_LIMIT:
                    _METEO_CACHE.pop(_METEO_CACHE_ORDER.pop(0), None)
        return run

    with ThreadPoolExecutor(max_workers=min(3, len(stations))) as pool:
        runs = list(pool.map(fetch, stations))

    summaries = [
        (run.get("result") or {})
        for run in runs
        if isinstance(run, dict) and run.get("status") in ("success", "warning")
        and isinstance(run.get("result"), dict)
    ]
    if not summaries:
        return None

    def number(source: dict[str, Any], key: str) -> float | None:
        try:
            return float(source.get(key))
        except (TypeError, ValueError):
            return None

    # 风按矢量平均（风向是"来向"：北风 0° 从北边吹来，向量指向南）
    us: list[float] = []
    vs: list[float] = []
    for summary in summaries:
        speed = number(summary, "mean_wind_speed")
        direction = number(summary, "dominant_wind_direction")
        if speed is None or direction is None:
            continue
        rad = math.radians(direction)
        us.append(-speed * math.sin(rad))
        vs.append(-speed * math.cos(rad))
    mean_u = sum(us) / len(us) if us else 0.0
    mean_v = sum(vs) / len(vs) if vs else 0.0
    mean_speed = math.hypot(mean_u, mean_v)
    mean_direction = (math.degrees(math.atan2(-mean_u, -mean_v)) + 360.0) % 360.0 if us else None

    def avg(key: str) -> float | None:
        values = [v for v in (number(s, key) for s in summaries) if v is not None]
        return round(sum(values) / len(values), 4) if values else None

    merged = dict(summaries[0])
    merged.update(
        {
            "mean_wind_speed": round(mean_speed, 4) if us else None,
            "dominant_wind_direction": round(mean_direction, 2) if mean_direction is not None else None,
            "dominant_wind_direction_label": wind_direction_label(mean_direction),
            "upwind_direction": round(mean_direction, 2) if mean_direction is not None else None,
            "downwind_direction": round((mean_direction + 180.0) % 360.0, 2) if mean_direction is not None else None,
            "mean_humidity": avg("mean_humidity"),
            "mean_temperature": avg("mean_temperature"),
            "total_precipitation": avg("total_precipitation"),
            "weather_risk": avg("weather_risk"),
            "static_wind_score": avg("static_wind_score"),
            "wind_shift_score": avg("wind_shift_score"),
            "humidity_risk_score": avg("humidity_risk_score"),
            "rain_removal_score": avg("rain_removal_score"),
            "insufficient_rain_removal_score": avg("insufficient_rain_removal_score"),
            "mean_cloud_cover": avg("mean_cloud_cover"),
        }
    )

    # 逐小时序列：各站同一时段、按时间戳对齐逐字段平均（风同样走矢量）
    series_by_time: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        for hour in summary.get("hourly_series") or []:
            series_by_time.setdefault(str(hour.get("time")), []).append(hour)

    merged_series: list[dict[str, Any]] = []
    for stamp, hours in sorted(series_by_time.items()):
        speeds = [h.get("wind_speed") for h in hours if isinstance(h.get("wind_speed"), (int, float))]
        dirs = [h.get("wind_direction") for h in hours if isinstance(h.get("wind_direction"), (int, float))]

        def hour_avg(key: str) -> float | None:
            values = [h.get(key) for h in hours if isinstance(h.get(key), (int, float))]
            return round(sum(values) / len(values), 3) if values else None

        hour_u = hour_v = 0.0
        if speeds and dirs:
            for speed, direction in zip(speeds, dirs):
                rad = math.radians(direction)
                hour_u += -speed * math.sin(rad)
                hour_v += -speed * math.cos(rad)
            hour_u /= len(speeds)
            hour_v /= len(speeds)
        hour_direction = (math.degrees(math.atan2(-hour_u, -hour_v)) + 360.0) % 360.0 if speeds and dirs else None

        row = dict(hours[0])
        row["wind_speed"] = round(math.hypot(hour_u, hour_v), 3) if speeds and dirs else None
        row["wind_direction"] = round(hour_direction, 1) if hour_direction is not None else None
        for key in ("humidity", "precipitation", "temperature", "cloud_cover"):
            row[key] = hour_avg(key)
        merged_series.append(row)

    merged["hourly_series"] = merged_series
    merged["hourly_count"] = len(merged_series)
    merged["station_count"] = len(summaries)
    merged["per_station"] = {
        (station.get("station_name_zh") or station["station_name"]): {
            "mean_wind_speed": number(summary, "mean_wind_speed"),
            "dominant_wind_direction": number(summary, "dominant_wind_direction"),
        }
        for station, summary in zip(stations, summaries)
        if summary
    }

    return {
        "agent_name": "MeteorologyAgent",
        "mode": "multi_station_merge",
        "status": "success",
        "result": merged,
        "created_at": created,
        "meta": {"task_id": task.get("task_id"), "stations": len(summaries)},
    }


def _combined_air_quality(task: dict[str, Any]) -> dict[str, Any]:
    """对全部 EPD 监测站（18 个）各跑一次空气质量 Agent，异常分取平均、证据合并。

    数据源是 EPD EPICDI 逐小时库。travel_agent 只消费 ``result.anomaly_score``，
    但返回结构保持与 Agent 契约一致，前端审计栏也能直接展示。
    """
    from datetime import datetime, timezone

    created = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    scores: list[float] = []
    evidence: list[str] = []
    totals: dict[str, float] = {}

    for station in epd_stations():
        run = _air_agent.run(
            {
                "task_id": f"{task.get('task_id')}_air_{station['station_id']}",
                "station_name": station["station_name"],
                "start_time": task.get("start_time"),
                "end_time": task.get("end_time"),
            }
        )
        result = run.get("result") or {}
        name = station.get("station_name_zh") or station["station_name"]
        quality = run.get("data_quality") or {}
        # 该时段查不到记录的站（比如选了数据库覆盖范围外的日期）返回
        # anomaly_score=0.0 的占位值——直接平均会把整体拉向 0，极端情况下
        # 18 个站全无数据时风险变成 0，暴露场就整个消失了。
        # 这种站必须跳过，让有数据的站决定平均分；全无数据时返回 None，
        # 让 travel_agent 退回默认源强度，而不是当作"空气极好"。
        has_data = (
            run.get("status") == "success"
            and float(quality.get("missing_rate", 1.0)) < 1.0
            and isinstance(result.get("anomaly_score"), (int, float))
        )
        anomaly = result.get("anomaly_score")
        if has_data:
            scores.append(float(anomaly))
            evidence.append(f"{name}：异常评分 {float(anomaly):.2f}（EPD 逐小时数据）")
            for pollutant, detail in (result.get("pollutant_details") or {}).items():
                value = (detail or {}).get("avg")
                if isinstance(value, (int, float)):
                    totals[pollutant] = totals.get(pollutant, 0.0) + float(value)
        else:
            evidence.append(f"{name}：该时段无 EPD 记录")

    count = max(1, len(epd_stations()))
    pollutant_avgs = {key: round(total / len(scores), 2) for key, total in totals.items()}
    avg_score = round(sum(scores) / len(scores), 4) if scores else None
    if avg_score is None:
        status = "unknown"
    elif avg_score >= 0.7:
        status = "abnormal"
    elif avg_score >= 0.4:
        status = "moderate"
    else:
        status = "normal"

    return {
        "agent_name": "AirQualityAgent",
        "status": "success" if scores else "warning",
        "result": {
            "anomaly_score": avg_score,
            "pollution_status": status,
            "main_pollutants": [],
            "pollutant_averages": pollutant_avgs,
            "station_count": len(scores),
        },
        "evidence": evidence or ["未取得任何监测站的空气质量数据。"],
        "confidence": round(0.7 + 0.05 * min(len(scores), 3), 2),
        "data_quality": {
            "missing_rate": round(1 - len(scores) / count, 3),
            "note": "多站联合取数：异常评分为各 EPD 监测站平均。",
        },
        "created_at": created,
        "meta": {"task_id": task.get("task_id"), "stations": len(scores)},
    }


def _combined_traffic(task: dict[str, Any]) -> dict[str, Any]:
    """对有 ATC 映射的全部 EPD 监测站各跑一次交通 Agent，压力分取平均。

    手工映射只登记了 3 个路边站；其余站由
    ``data_process/map_epd_to_atc.py`` 按官方 ATC 站点坐标就近补齐
    （2 km 内）。2 km 内确实没有 ATC 统计站的（塔门、东涌等偏远站）
    如实跳过，不硬凑。
    """
    from datetime import datetime, timezone

    created = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    scores: list[float] = []
    evidence: list[str] = []
    per_station: dict[str, float] = {}

    traffic_stations = [item for item in epd_stations() if item.get("atc_station_ids")]
    for station in traffic_stations:
        run = _traffic_agent.run(
            {
                "task_id": f"{task.get('task_id')}_traffic_{station['station_id']}",
                "station_name": station["station_name"],
                "start_time": task.get("start_time"),
                "end_time": task.get("end_time"),
            }
        )
        result = run.get("result") or {}
        pressure = result.get("traffic_pressure_score")
        name = station.get("station_name_zh") or station["station_name"]
        if isinstance(pressure, (int, float)):
            scores.append(float(pressure))
            per_station[name] = round(float(pressure), 4)
            dominant = ", ".join((result.get("dominant_vehicle_types") or [])[:2]) or "—"
            evidence.append(f"{name}：交通压力 {float(pressure):.2f}（ATC 车型构成，主车型 {dominant}）")
        else:
            evidence.append(f"{name}：无对应 ATC 记录")

    avg = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "agent_name": "TrafficAgent",
        "status": "success" if scores else "warning",
        "result": {
            "traffic_pressure_score": avg,
            "per_station": per_station,
        },
        "evidence": evidence or ["未取得任何路边站的 ATC 交通数据。"],
        "confidence": round(0.7 + 0.05 * min(len(scores), 3), 2),
        "data_quality": {"missing_rate": 0.0 if scores else 1.0},
        "created_at": created,
        "meta": {"task_id": task.get("task_id"), "stations": len(scores)},
    }

# 解析并序列化一份 1.6 MB 的 JSON 没必要每次都做，按 mtime 缓存住
_case_cache: dict[str, Any] = {"key": None, "text": None}


def _case_output_path() -> Path:
    override = os.environ.get("TRAVEL_CASE_OUTPUT", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CASE_OUTPUT


def create_blueprint(agent: TravelAgent | None = None) -> Blueprint:
    travel = agent or TravelAgent()
    blueprint = Blueprint("travel_agent", __name__)

    @blueprint.get("/health")
    def health() -> Any:
        return jsonify({"agent_name": travel.agent_name, "status": "ok"})

    @blueprint.get("/case")
    def case_output() -> Any:
        """
        返回 examples/run_travel_case.py 生成的完整案例输出。

        前端不再打包一份裁剪过的副本：那样会与 Agent 的真实输出脱节
        （网格被隔点抽稀、候选路线被删到每种方式一条）。
        这里按原文件原样吐出，保证页面看到的就是脚本写出来的东西。
        """
        path = _case_output_path()
        if not path.is_file():
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"未找到案例输出文件：{path}",
                        "hint": "先运行 python examples/run_travel_case.py 生成，"
                        "或用环境变量 TRAVEL_CASE_OUTPUT 指定路径",
                    }
                ),
                404,
            )

        stat = path.stat()
        cache_key = f"{path}::{stat.st_mtime_ns}::{stat.st_size}"
        if _case_cache["key"] != cache_key:
            text = path.read_text(encoding="utf-8")
            json.loads(text)  # 早点发现坏文件，别把非法 JSON 丢给前端
            _case_cache["key"] = cache_key
            _case_cache["text"] = text

        return Response(
            _case_cache["text"],
            mimetype="application/json",
            headers={"X-Case-Path": str(path), "Cache-Control": "no-store"},
        )

    @blueprint.get("/profiles")
    def profiles() -> Any:
        """列出可用的出行习惯画像，供前端下拉选择。"""
        config = load_travel_config()
        return jsonify(
            {
                "count": len(list_profiles(config)),
                "profiles": [
                    {
                        "profile_id": profile.get("profile_id"),
                        "profile_name": profile.get("profile_name"),
                        "description": profile.get("description"),
                        "preferred_modes": profile.get("preferred_modes"),
                        "max_walk_km": profile.get("max_walk_km"),
                        "max_trip_minutes": profile.get("max_trip_minutes"),
                        "time_flexibility_minutes": profile.get("time_flexibility_minutes"),
                    }
                    for profile in list_profiles(config)
                ],
            }
        )

    @blueprint.post("/plan")
    def plan() -> Any:
        """
        出行建议与路线推荐。起终点由请求体给出，任意 WGS84 坐标均可。

        请求体（JSON）：
        {
          "task_id":        "TRAVEL_HK_MK_TST",
          "origin":         {"name": "尖沙咀站", "lat": 22.2977,  "lon": 114.17218},
          "destination":    {"name": "太子站",   "lat": 22.32441, "lon": 114.16828},
          "start_time":     "2023-08-15 08:00:00",
          "end_time":       "2023-08-15 11:00:00",
          "travel_profile": "commuter",
          "pollution_source":  {"station_id": "MK", "lat": 22.3193, "lon": 114.1694},
          "pollution_sources": [ {"lat": ..., "lon": ..., "strength": 0.9} ],
          "air_quality_risk": 0.94,
          "traffic_pressure_score": 0.7363
        }

        可选字段：
          pollution_sources   —— 多个污染事件点位；与 pollution_source 合并使用
          pollution_source    —— 单个污染事件点位；也可省略，改由任务根对象给 station_id/lat/lon
          air_quality_risk    —— 站点源强度（0-1），缺省取 air_quality_result.anomaly_score
          traffic_pressure_score —— 交通走廊源强度（0-1），缺省取 traffic_result.traffic_pressure_score
          meteorology_result  —— 已算好的气象 Agent 响应，传入后不再重复请求气象接口
          air_quality_result  —— 空气质量 Agent 响应
          traffic_result      —— 交通 Agent 响应

        响应中 result.pollution_field.grid 是暴露场的采样点，供前端直接绘制
        污染分布图层；result.pollution_advice 给出避污建议。
        """
        payload: dict[str, Any] = request.get_json(silent=True) or {}

        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload

        # 前端没有指定污染源时，默认把**全部路边监测站**（旺角/铜锣湾/中环）
        # 都作为污染输入。出行建议关心的是路边交通暴露，只锚定一个站会让
        # "换站点"变成用户必须做的无意义选择。
        # 注意不能把 18 个一般站也塞进来：它们散布全港（塔门、东涌……），
        # 暴露场网格会被外扩成整个香港，每次重建都要算几十万个格点，规划从
        # 几秒恶化到一分钟以上；而且一般站本就代表区域背景，不是路边源。
        if not task.get("pollution_sources") and not task.get("pollution_source"):
            task["pollution_sources"] = [
                {
                    "station_id": item["station_id"],
                    "station_name": item["station_name"],
                    "lat": item["lat"],
                    "lon": item["lon"],
                }
                for item in epd_stations()
                if item.get("station_type") == "roadside"
            ]

        # 出行建议必须调用**全部**数据接口。空气质量与交通的 Agent 结果
        # 没随请求带来时，就在这里用真实数据源（EPD 逐小时库 / ATC 交通库）
        # 现场各算一遍 —— 不让请求体里手填的风险值（例如场景示例的 0.94）
        # 顶掉真实输入。travel_agent 的取值优先级是显式值 > Agent 结果 > 缺省，
        # 所以这两份结果只在调用方没给时才生效。
        if not payload.get("air_quality_result"):
            payload["air_quality_result"] = _combined_air_quality(task)
        if not payload.get("traffic_result"):
            payload["traffic_result"] = _combined_traffic(task)
        # 气象：对全部路边站分别取数再合成，替代"只取起点一个点"的旧做法
        if not payload.get("meteorology_result"):
            payload["meteorology_result"] = _combined_meteorology(task)

        errors = validate_task(task)
        if errors:
            return jsonify({"status": "error", "message": "；".join(errors)}), 400

        result = travel.run(
            task,
            meteorology_result=payload.get("meteorology_result"),
            air_quality_result=payload.get("air_quality_result"),
            traffic_result=payload.get("traffic_result"),
        )
        return jsonify(result), (200 if result["status"] != "error" else 400)

    return blueprint


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_ENSURE_ASCII"] = False
    app.register_blueprint(create_blueprint(), url_prefix="/api/travel")
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5053, debug=False)
