"""
Meteorology Agent Web 服务层。

路由只负责：解析请求 → 调用 Agent → 包装响应。
所有评分、ETL、规则逻辑均在 agents/ 和 tools/ 中，本文件不重复。
所有输入校验统一由 agents.meteorology_agent.validate_task 完成，本层不重复实现。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

# 保证从项目根目录可导入
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.meteorology_agent import (
    _parse_hour,
    _LAT_RANGE,
    _LON_RANGE,
    _MIN_WINDOW_HOURS,
    _MAX_WINDOW_HOURS,
    batch_run,
    run,
    validate_task,
)
from data_process.weather_etl import dataframe_records_for_json, raw_to_standard_hourly
from tools.open_meteo_client import fetch_with_fallback
from config.field_schema import API_TIMEZONE
from spatial_agent.api import create_blueprint as create_spatial_blueprint
from api.travel_api import create_blueprint as create_travel_blueprint
from api.analysis_api import create_blueprint as create_analysis_blueprint
from api.chat_api import create_blueprint as create_chat_blueprint
from api.stations import load_stations

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_ENSURE_ASCII"] = False   # 中文不转义
app.register_blueprint(create_spatial_blueprint(), url_prefix="/api/spatial")
app.register_blueprint(create_travel_blueprint(), url_prefix="/api/travel")
# Coordinator 整链路：POST /api/analyze 一次跑完 7 个 Agent
app.register_blueprint(create_analysis_blueprint(), url_prefix="/api")
# AI 研判问答：POST /api/chat（自由问题交给大模型，结合链路上下文作答）
app.register_blueprint(create_chat_blueprint(), url_prefix="/api")

# ── 跨域 ──────────────────────────────────────────────────────────────────
# 前端开发服务器（Vite，默认 5173/5174）与本服务不同源（来源 127.0.0.1:8000）。
# 不加这几个响应头，浏览器会在发出请求前直接拦掉，前端只会静默降级回 mock 数据，
# 表面看页面正常、实际一次后端都没调到。生产环境由反向代理同源提供，不受影响。
DEV_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
}


@app.after_request
def add_cors_headers(response: Any) -> Any:
    """为本地前端开发来源补上 CORS 响应头，并正确声明预检结果。"""
    origin = request.headers.get("Origin")
    if origin in DEV_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _json_error(message: str, status: int = 400) -> tuple[Any, int]:
    return jsonify({"status": "error", "message": message}), status


def _build_task_from_body(body: dict[str, Any]) -> dict[str, Any]:
    """从请求体提取单任务字段（排除 tasks 批处理键，保留 None 值供校验层检测缺失）。"""
    return {k: v for k, v in body.items() if k != "tasks"}


def _validate_hourly_params(
    lat_str: str | None,
    lon_str: str | None,
    start_time: str | None,
    end_time: str | None,
) -> tuple[float | None, float | None, str | None, str | None, list[str]]:
    """
    校验 /hourly 接口的查询参数，返回 (lat, lon, start_time, end_time, errors)。
    与 validate_task 保持相同的范围和时间约束。
    """
    errors: list[str] = []

    # 必填检查
    missing = [p for p, v in [("lat", lat_str), ("lon", lon_str),
                               ("start_time", start_time), ("end_time", end_time)] if not v]
    if missing:
        errors.append(f'缺少必填参数: {", ".join(missing)}')
        return None, None, None, None, errors

    # lat/lon 数值解析
    try:
        lat = float(lat_str)  # type: ignore[arg-type]
    except ValueError:
        errors.append("lat 必须是数字")
        lat = None
    try:
        lon = float(lon_str)  # type: ignore[arg-type]
    except ValueError:
        errors.append("lon 必须是数字")
        lon = None

    if errors:
        return None, None, None, None, errors

    # 坐标范围
    if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]):  # type: ignore[operator]
        errors.append(f"lat={lat} 超出合法范围 [{_LAT_RANGE[0]}, {_LAT_RANGE[1]}]")
    if not (_LON_RANGE[0] <= lon <= _LON_RANGE[1]):  # type: ignore[operator]
        errors.append(f"lon={lon} 超出合法范围 [{_LON_RANGE[0]}, {_LON_RANGE[1]}]")

    # 时间解析与约束
    start_ts = _parse_hour(start_time)  # type: ignore[arg-type]
    end_ts = _parse_hour(end_time)  # type: ignore[arg-type]
    if start_ts is None:
        errors.append(f"start_time={start_time!r} 无法解析为合法时间")
    if end_ts is None:
        errors.append(f"end_time={end_time!r} 无法解析为合法时间")

    if start_ts is not None and end_ts is not None:
        if end_ts < start_ts:
            errors.append(f"end_time 早于 start_time，时间段无效")
        else:
            window_hours = (end_ts - start_ts).total_seconds() / 3600
            if window_hours < _MIN_WINDOW_HOURS:
                errors.append(f"时间窗口 {window_hours:.1f}h 不足最小值 {_MIN_WINDOW_HOURS}h")
            elif window_hours > _MAX_WINDOW_HOURS:
                errors.append(f"时间窗口 {window_hours:.0f}h 超出最大值 {_MAX_WINDOW_HOURS}h")

    if errors:
        return None, None, None, None, errors

    # 返回整点化时间字符串
    fmt = "%Y-%m-%d %H:%M:%S"
    return lat, lon, start_ts.strftime(fmt), end_ts.strftime(fmt), []  # type: ignore[union-attr]


# ── POST /api/meteorology/analyze ──────────────────────────────────────────

@app.post("/api/meteorology/analyze")
def analyze():
    """
    气象风险分析接口。

    请求体（JSON）支持两种模式：

    【单任务】
    {
      "task_id":    "HK_MK_20260723_0800",
      "lat":        22.3193,
      "lon":        114.1694,
      "start_time": "2026-07-23 08:00:00",
      "end_time":   "2026-07-23 11:00:00",
      "station_id": "可选",
      "station_name": "可选"
    }

    【批处理】tasks 字段存在时自动走批处理模式
    {
      "tasks": [
        {"task_id": ..., "lat": ..., "lon": ..., "start_time": ..., "end_time": ...},
        ...
      ]
    }

    响应体：
    {
      "mode":   "single" | "batch",
      "count":  <int>,
      "result": <agent_json> | [<agent_json>, ...]
    }
    """
    body: dict[str, Any] = request.get_json(silent=True) or {}

    # 批处理模式：校验完全委托给 batch_run → run → validate_task
    if "tasks" in body:
        tasks = body["tasks"]
        if not isinstance(tasks, list) or len(tasks) == 0:
            return _json_error('"tasks" 必须是非空列表')
        results = batch_run(tasks)
        return jsonify({
            "mode": "batch",
            "count": len(results),
            "result": results,
        })

    # 单任务模式：校验完全委托给 run → validate_task，不在此重复
    task = _build_task_from_body(body)
    result = run(task)
    # 若 validate_task 检测到错误，run() 返回 status=error，HTTP 仍返回 200
    # （保持接口幂等，错误信息在 body 中）
    return jsonify({
        "mode": "single",
        "count": 1,
        "result": result,
    })


# ── GET /api/meteorology/hourly ────────────────────────────────────────────

@app.get("/api/meteorology/hourly")
def hourly():
    """
    标准小时级气象序列接口。

    查询参数（必填）：lat, lon, start_time, end_time
    查询参数（可选）：station_id（默认 UNKNOWN）

    时区固定为 Asia/Hong_Kong，不接受外部 timezone 参数。
    时间输入与 /analyze 接口遵循相同校验规则（lat/lon 范围、时间整点化、窗口约束）。

    响应体：
    {
      "station_id":    "MK",
      "lat":           22.3193,
      "lon":           114.1694,
      "start_time":    "...",
      "end_time":      "...",
      "hourly_count":  4,
      "fallback_used": false,
      "records":       [ {标准小时列}, ... ]
    }
    """
    lat, lon, start_time, end_time, errors = _validate_hourly_params(
        request.args.get("lat"),
        request.args.get("lon"),
        request.args.get("start_time"),
        request.args.get("end_time"),
    )
    if errors:
        return _json_error("; ".join(errors))

    station_id = request.args.get("station_id", "UNKNOWN")
    fallback_csv = ROOT / "data" / "sample_weather_mong_kok.csv"

    try:
        raw_df, fallback_used, api_error = fetch_with_fallback(
            lat, lon, start_time, end_time, fallback_csv, timezone=API_TIMEZONE
        )
    except Exception as exc:
        logger.exception("hourly: fetch_with_fallback 异常")
        return _json_error(f"数据获取失败: {exc}", status=502)

    hourly_df = raw_to_standard_hourly(raw_df, station_id, lat, lon)

    if hourly_df.empty:
        return jsonify({
            "station_id": station_id,
            "lat": lat,
            "lon": lon,
            "start_time": start_time,
            "end_time": end_time,
            "hourly_count": 0,
            "fallback_used": fallback_used,
            "records": [],
        })

    records = dataframe_records_for_json(hourly_df)

    return jsonify({
        "station_id": station_id,
        "lat": lat,
        "lon": lon,
        "start_time": start_time,
        "end_time": end_time,
        "hourly_count": len(records),
        "fallback_used": fallback_used,
        "records": records,
    })


# ── GET /api/meteorology/stations ──────────────────────────────────────────

@app.get("/api/meteorology/stations")
def meteorology_stations():
    """列出项目里登记的监测站点（18 个：3 路边 + 15 一般），供前端站点选择器使用。"""
    stations = load_stations()
    return jsonify(
        {
            "count": len(stations),
            "source": "spatial_agent/data/stations.csv + data/air_quality.db",
            "stations": stations,
        }
    )


# ── 健康检查 ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """服务存活探针。"""
    return jsonify({"status": "ok"})


# ── 本地启动 ──────────────────────────────────────────────────────────────
# 端口取 8000，与 frontend/.env.development 的 VITE_API_BASE_URL 对齐。
# 不用 5000：macOS 的 AirPlay 接收器会占用该端口导致起不来。
def warm_up() -> None:
    """预热首次请求才会付出的一次性开销。

    两处，实测合计约 50 s，全部落在第一个用户请求上：

    1. **出行 Agent 的重资产**：路网图、轨道站、轨道邻接表都是惰性加载且带模块级
       缓存。第一次要连读 5.7 MB 路网 GeoJSON 并建图。
    2. **气象 ETL 路径**：同一个进程里第一次 `meteorology_agent.run()` 实测 24.6 s，
       第二、三次只要 2.7 s / 2.3 s——差的是 pandas 时区处理等一次性初始化，
       不是网络也不是模型。这一路没有缓存，所以只能靠预热把进程内的开销摊掉。

    不预热的话首次请求要 30 s 上下，很容易撞上前端超时并静默降级回 mock 数据。
    """
    import time  # noqa: PLC0415 - 仅启动期使用

    try:
        from agents.travel_agent import TravelAgent  # noqa: PLC0415 - 启动期导入
        from agents.meteorology_agent import run as meteorology_run  # noqa: PLC0415
        from agents.coordinator_min import CoordinatorMin  # noqa: PLC0415

        started = time.time()
        agent = TravelAgent()
        graph = agent.road_graph
        stations = agent.rail_stations
        links = agent.rail_links
        logger.info(
            "预热完成：路网 %d 节点 / %d 边，轨道 %d 站 / %d 条邻接（%.1f s）",
            len(graph),
            len(graph.edges),
            len(stations),
            len(links),
            time.time() - started,
        )

        started = time.time()
        meteorology_run(
            {
                "task_id": "WARMUP",
                "station_id": "MK",
                "station_name": "Mong Kok Roadside",
                "lat": 22.3193,
                "lon": 114.1694,
                "start_time": "2023-08-15 08:00:00",
                "end_time": "2023-08-15 11:00:00",
            }
        )
        logger.info("预热完成：气象 ETL 路径（%.1f s）", time.time() - started)

        # Coordinator 整链路：这里只把各 Agent 实例建起来（空间 Agent 要读路网建图），
        # 不跑 analyze —— 整链路要联网取气象，放启动期会把服务拉起时间拖得很长。
        started = time.time()
        coordinator = CoordinatorMin()
        logger.info("预热完成：Coordinator 各 Agent 实例（%.1f s）", time.time() - started)
        del coordinator
    except Exception as exc:  # 预热失败不应阻止服务启动
        logger.warning("预热失败（服务仍会启动，首次请求会较慢）：%s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # 只在真正对外服务的进程里预热。
    # 开了自动重载后 Werkzeug 会另起一个监视用的父进程，父进程也会执行到这里；
    # 用 WERKZEUG_RUN_MAIN 区分（子进程被重载器启动时该变量为 "true"），
    # 否则那 26 秒的预热会白跑一遍，启动时间翻倍。
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        warm_up()
    # use_reloader=True：改完后端代码自动重启。
    #
    # 这不是"顺手加的"——没有重载器时，Flask 会**静默地继续用旧代码**服务。
    # 实际踩过：新增 /api/travel/case 之后进程还在跑旧版本，
    # 前端拿到 404，看起来像"接口没实现"，实际只是进程没重启。
    # 调试器仍然关闭（debug=False），只开重载。
    app.run(
        host="127.0.0.1",
        port=8000,
        debug=False,
        threaded=True,
        use_reloader=True,
    )
