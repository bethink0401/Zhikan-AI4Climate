"""Meteorology Agent：串联 API、ETL、规则与统一 JSON 输出。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data_process.weather_etl import raw_to_standard_hourly
from tools import meteorology_tools as mt
from tools.open_meteo_client import fetch_with_fallback
from tools.trajectory_tools import forecast_wind_vectors, predict_pollution_path
from tools.parcel_lifetime import build_lifetime_model, load_parcel_rules, parcel_initial_conditions
from tools.propagation_validation import attach_station_validation

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FALLBACK_CSV = PROJECT_ROOT / "data" / "sample_weather_mong_kok.csv"
DEFAULT_RULES = PROJECT_ROOT / "config" / "meteorology_rules.json"

AGENT_NAME = "MeteorologyAgent"

# data_quality.source 标识
_SOURCE_API      = "Open-Meteo Archive API"
_SOURCE_FALLBACK = "Local Fallback CSV"
_SOURCE_NONE     = "None"

# ---------------------------------------------------------------------------
# 输入契约常量
# 详细说明见 docs/api_contract.md
# ---------------------------------------------------------------------------

_REQUIRED_STR_FIELDS = ("task_id", "start_time", "end_time")
_REQUIRED_NUM_FIELDS = ("lat", "lon")

_LAT_RANGE = (-90.0, 90.0)
_LON_RANGE = (-180.0, 180.0)

_MIN_WINDOW_HOURS = 1
_MAX_WINDOW_HOURS = 24 * 7

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _parcel_inputs(task: dict[str, Any], air_quality_result: dict[str, Any] | None,
                   origin_time: str | None = None) -> dict[str, Any]:
    origin_time = origin_time or task["end_time"]
    def aligned(value: dict[str, Any]) -> dict[str, Any]:
        stamp = value.get("observed_at")
        if stamp and pd.Timestamp(stamp).strftime(_TIME_FMT) != pd.Timestamp(origin_time).strftime(_TIME_FMT):
            return {"status": "unavailable", "pollutants": {},
                    "reason": "污染物末次观测与传播起算小时不一致"}
        return value

    if air_quality_result is not None:
        air = air_quality_result.get("result") or {}
        meta = air_quality_result.get("meta") or {}
        source_station = str(meta.get("station_name") or "").lower().replace(" roadside", "").strip()
        target_station = str(task.get("station_name") or "").lower().replace(" roadside", "").strip()
        if source_station and target_station and source_station != target_station:
            return {"status": "unavailable", "pollutants": {}, "reason": "污染物观测站与传播起点站不一致"}
        initial = air.get("parcel_start_conditions") if origin_time == task.get("start_time") else air.get("parcel_initial_conditions")
        if initial:
            return aligned(initial)
        series = [row for row in air.get("hourly_series") or [] if str(row.get("time") or "")[:19] <= origin_time]
        return aligned(parcel_initial_conditions(series) if series else air.get("parcel_initial_conditions") or parcel_initial_conditions([]))
    # Standalone meteorology requests use the same station-owned EPD directory
    # as the coordinator; no station name means no safe concentration lookup.
    if not task.get("station_name"):
        return parcel_initial_conditions([])
    from tools.air_quality_tools import open_db, query_air_quality_data

    try:
        end = datetime.fromisoformat(origin_time)
        history_start = (end - timedelta(days=load_parcel_rules()["reference_lookback_days"])).strftime(_TIME_FMT)
        conn = open_db()
        try:
            rows = query_air_quality_data(conn, task["station_name"], origin_time, origin_time)
            history = query_air_quality_data(conn, task["station_name"], history_start, origin_time)
        finally:
            conn.close()
        return aligned(parcel_initial_conditions(rows, history))
    except (FileNotFoundError, sqlite3.Error, ValueError, TypeError):
        logger.warning("污染物寿命初值不可用，仅提供风场传播路径", exc_info=True)
        return parcel_initial_conditions([])


def _parse_hour(time_str: str) -> pd.Timestamp | None:
    """将时间字符串解析为整点 Timestamp，解析失败返回 None。"""
    try:
        ts = pd.Timestamp(time_str)
        return ts.floor("h")
    except Exception:
        return None


def validate_task(task: dict[str, Any]) -> list[str]:
    """
    完整校验 task 输入契约，返回错误列表（空列表表示合法）。

    校验规则（与 docs/api_contract.md 保持一致）：
      1. task_id / start_time / end_time 必填且为非空字符串
      2. lat / lon 必须同时存在且为数值
      3. lat ∈ [-90, 90]，lon ∈ [-180, 180]
      4. start_time / end_time 可被解析为合法时间
      5. 整点化后 end_time >= start_time
      6. 时间窗口长度 ∈ [1h, 168h]
    """
    errors: list[str] = []

    # 1. 必填字符串字段
    for field in _REQUIRED_STR_FIELDS:
        if field not in task or task[field] is None:
            errors.append(f"缺少必填字段: {field!r}")
        elif not isinstance(task[field], str):
            errors.append(
                f"字段 {field!r} 类型错误，期望 str，"
                f"实际 {type(task[field]).__name__}"
            )
        elif not str(task[field]).strip():
            errors.append(f"字段 {field!r} 不能为空字符串")

    # 2. lat / lon 必须同时存在且为数值
    lat_present = "lat" in task and task["lat"] is not None
    lon_present = "lon" in task and task["lon"] is not None
    if not lat_present or not lon_present:
        errors.append("lat 和 lon 必须同时提供")
    else:
        for field in _REQUIRED_NUM_FIELDS:
            if not isinstance(task[field], (int, float)):
                errors.append(
                    f"字段 {field!r} 类型错误，期望 int/float，"
                    f"实际 {type(task[field]).__name__}"
                )

    if errors:
        return errors

    # 3. 坐标范围
    lat = float(task["lat"])
    lon = float(task["lon"])
    if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]):
        errors.append(f"lat={lat} 超出合法范围 [{_LAT_RANGE[0]}, {_LAT_RANGE[1]}]")
    if not (_LON_RANGE[0] <= lon <= _LON_RANGE[1]):
        errors.append(f"lon={lon} 超出合法范围 [{_LON_RANGE[0]}, {_LON_RANGE[1]}]")

    # 4. 时间字段可解析
    start_ts = _parse_hour(task["start_time"])
    end_ts   = _parse_hour(task["end_time"])
    if start_ts is None:
        errors.append(f"start_time={task['start_time']!r} 无法解析为合法时间")
    if end_ts is None:
        errors.append(f"end_time={task['end_time']!r} 无法解析为合法时间")

    # 5 & 6. 时间逻辑
    if start_ts is not None and end_ts is not None:
        if end_ts < start_ts:
            errors.append(
                f"end_time（{end_ts}）早于 start_time（{start_ts}），时间段无效"
            )
        else:
            window_hours = (end_ts - start_ts).total_seconds() / 3600
            if window_hours < _MIN_WINDOW_HOURS:
                errors.append(
                    f"时间窗口 {window_hours:.1f}h 不足最小值 {_MIN_WINDOW_HOURS}h"
                    "（start_time 与 end_time 整点化后相同或差距不足 1 小时）"
                )
            elif window_hours > _MAX_WINDOW_HOURS:
                errors.append(
                    f"时间窗口 {window_hours:.0f}h 超出最大值 "
                    f"{_MAX_WINDOW_HOURS}h（{_MAX_WINDOW_HOURS // 24} 天）"
                )

    return errors


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _now_hk_str() -> str:
    """当前香港本地时间字符串。"""
    return datetime.now().strftime(_TIME_FMT)


def _safe_float(value: Any) -> float | None:
    """安全转为 Python float，None/NaN → None。"""
    if value is None:
        return None
    try:
        v = float(value)
        import math
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    """安全转为 Python int，None → None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_data_quality(
    missing_rate: float,
    fallback_used: bool,
    api_error: str | None,
    extra_notes: list[str] | None = None,
) -> dict[str, Any]:
    """构造统一的 data_quality 对象。"""
    source = _SOURCE_FALLBACK if fallback_used else _SOURCE_API
    note = mt.build_data_quality_note(
        missing_rate, fallback_used, api_error, extra_notes
    )
    return {
        "missing_rate": float(round(missing_rate, 4)),
        "fallback_used": bool(fallback_used),
        "source": source,
        "note": note,
    }


def _failed_response(
    message: str,
    task_id: str | None = None,
    fallback_used: bool = False,
    missing_rate: float = 1.0,
    source: str = _SOURCE_NONE,
    api_error: str | None = None,
) -> dict[str, Any]:
    """
    构造 status=failed 的统一 JSON。
    用于：输入校验错误、API+fallback 均失败、无可用数据等致命错误。
    """
    return {
        "agent_name": AGENT_NAME,
        "status": "failed",
        "result": {},
        "evidence": [message],
        "confidence": 0.0,
        "data_quality": {
            "missing_rate": float(round(missing_rate, 4)),
            "fallback_used": bool(fallback_used),
            "source": source,
            "note": message,
        },
        "created_at": _now_hk_str(),
    }


def run(
    task: dict[str, Any],
    rules_path: Path | None = None,
    fallback_csv: Path | None = None,
    *,
    air_quality_result: dict[str, Any] | None = None,
    validation_db_path: Path | None = None,
) -> dict[str, Any]:
    """
    气象智能体主入口：接收 task 字典，返回统一 JSON。

    输出状态规则（见 docs/api_contract.md 输出契约）：
      - status=success  ：真实 API 成功，核心字段完整
      - status=warning  ：使用 fallback CSV，或核心字段部分缺失但仍能计算
      - status=failed   ：输入非法，或 API+fallback 均失败，或无可用数据
    """
    # ── 输入校验 ────────────────────────────────────────────────────────
    errors = validate_task(task)
    if errors:
        return _failed_response(
            "task 输入不合法: " + "; ".join(errors),
            task_id=task.get("task_id"),  # type: ignore[arg-type]
        )

    task_id    = str(task["task_id"])
    station_id = str(task.get("station_id", "UNKNOWN"))
    lat        = float(task["lat"])
    lon        = float(task["lon"])
    start_time = _parse_hour(task["start_time"]).strftime(_TIME_FMT)  # type: ignore[union-attr]
    end_time   = _parse_hour(task["end_time"]).strftime(_TIME_FMT)    # type: ignore[union-attr]

    rules    = mt.load_rules(rules_path or DEFAULT_RULES)
    timezone = rules.get("timezone", "Asia/Hong_Kong")
    csv_path = fallback_csv or DEFAULT_FALLBACK_CSV

    try:
        # ── 1. 数据获取（含兜底）──────────────────────────────────────
        lookback_start = (datetime.fromisoformat(start_time) - timedelta(
            hours=load_parcel_rules()["wind_lookback_hours"])).strftime(_TIME_FMT)
        raw_df, fallback_used, api_error = fetch_with_fallback(
            lat, lon, lookback_start, end_time, csv_path, timezone=timezone
        )

        # ── 2. ETL ────────────────────────────────────────────────────
        all_hourly = raw_to_standard_hourly(raw_df, station_id, lat, lon)
        stamps = all_hourly["time"].astype(str).str[:19]
        hourly_df = all_hourly.loc[(stamps >= start_time) & (stamps <= end_time)].copy()

        if hourly_df.empty:
            # API 和 fallback 均返回空数据 → failed
            return _failed_response(
                "无可用小时级气象数据（API 和 fallback 均未返回有效数据）",
                task_id=task_id,
                fallback_used=bool(fallback_used),
                missing_rate=1.0,
                source=_SOURCE_FALLBACK if fallback_used else _SOURCE_NONE,
                api_error=api_error,
            )

        # ── 3. 数据质量分析 ───────────────────────────────────────────
        missing_rate = mt.compute_missing_rate(hourly_df)

        # 核心字段是否存在部分缺失（missing_rate > 0 但 hourly_df 非空，仍可计算）
        core_field_missing = missing_rate > 0.0

        # 检查非核心字段缺失（weather_code_daily）并生成补充说明
        extra_notes: list[str] = []
        if "weather_code_daily" not in hourly_df.columns or \
                hourly_df["weather_code_daily"].isna().all():
            extra_notes.append("weather_code_daily 缺失（不影响评分）")

        # ── 4. 四维评分 ───────────────────────────────────────────────
        static_score, ev_static = mt.score_static_wind(hourly_df, rules)
        shift_score,  ev_shift  = mt.score_wind_shift(hourly_df, rules)
        hum_score,    ev_hum    = mt.score_humidity(hourly_df, rules)
        # score_rain_removal 返回 insufficient_rain_removal_score（高分=少雨=清除不足）
        insuf_rain_score, ev_rain = mt.score_rain_removal(hourly_df, rules)
        # rain_removal_score = 清除能力分（越高=清除越强=风险越低）
        rain_score = 1.0 - insuf_rain_score

        # aggregate_weather_risk 使用 insufficient_rain_removal_score（高分=风险大）
        weather_risk = mt.aggregate_weather_risk(
            static_score, shift_score, hum_score, insuf_rain_score, rules
        )
        risk_type = mt.risk_type_from_score(weather_risk, rules)

        # ── 5. 代表时刻（末小时）─────────────────────────────────────
        last = hourly_df.iloc[-1]
        ws_f = _safe_float(last.get("wind_speed"))
        wd_f = _safe_float(last.get("wind_direction"))

        up_txt, down_txt = mt.wind_up_downstream(wd_f)

        # ── 5b. 窗口聚合统计 ──────────────────────────────────────────
        agg = mt.compute_aggregate_stats(hourly_df)
        wc  = mt.compute_weather_code_summary(hourly_df)

        # dispersion_condition 使用窗口平均风速（与 weather_risk 一致）
        disp = mt.dispersion_condition(agg["mean_wind_speed"], rules)

        # ── 5c. 逐小时时序数据 ─────────────────────────────────────────
        # 生成 hourly_series，供前端绘制趋势图
        hourly_series = []
        for _, row in hourly_df.iterrows():
            hourly_series.append({
                "time": str(row.get("time")),
                "wind_speed": _safe_float(row.get("wind_speed")),
                "wind_direction": _safe_float(row.get("wind_direction")),
                "humidity": _safe_float(row.get("humidity")),
                "precipitation": _safe_float(row.get("precipitation")),
                "temperature": _safe_float(row.get("temperature")),
                "pressure_hpa": _safe_float(row.get("pressure_hpa")),
                "cloud_cover": _safe_float(row.get("cloud_cover")),
            })

        # ── 5d. 短时风向与污染输送路径预测 ────────────────────────────
        # 以最近小时风场进行圆统计预测，并以预测的风矢量逐小时平流。
        # 预测不可用时显式返回 unavailable，避免将静风误绘成传播路径。
        prediction_horizon = int((datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds() / 3600)
        prior_wind = all_hourly.loc[stamps <= start_time]
        wind_forecast = forecast_wind_vectors(prior_wind, horizon_hours=prediction_horizon,
                                              lookback_hours=load_parcel_rules()["wind_lookback_hours"])
        wind_forecast.update(origin_time=start_time, end_time=end_time, horizon_hours=prediction_horizon)
        origin_weather = prior_wind.iloc[-1] if not prior_wind.empty else {}
        initial_conditions = _parcel_inputs(task, air_quality_result, start_time)
        lifetime_model = build_lifetime_model(
            {key: origin_weather.get(key) for key in ("temperature", "humidity", "pressure_hpa")},
            initial_conditions,
        )
        lifetime_model["weather_observed_at"] = str(origin_weather.get("time") or "")
        pollution_path_prediction = predict_pollution_path(lon, lat, wind_forecast, lifetime_model=lifetime_model)
        pollution_path_prediction.update(simulation_start_time=start_time, requested_end_time=end_time,
                                          requested_horizon_hours=prediction_horizon)
        for step in pollution_path_prediction.get("steps") or []:
            step["time"] = (datetime.fromisoformat(start_time) + timedelta(hours=step["lead_hour"])).strftime(_TIME_FMT)
        completed_steps = pollution_path_prediction.get("steps") or []
        pollution_path_prediction["final_step"] = dict(completed_steps[-1]) if completed_steps else None
        pollution_path_prediction["actual_horizon_hours"] = completed_steps[-1]["lead_hour"] if completed_steps else 0
        pollution_path_prediction["actual_end_time"] = completed_steps[-1]["time"] if completed_steps else start_time
        attach_station_validation(pollution_path_prediction, {**task, "start_time": start_time, "end_time": end_time},
                                  initial_conditions, validation_db_path)

        # ── 6. 输出状态与置信度 ───────────────────────────────────────
        status     = mt.determine_status(
            fallback_used=bool(fallback_used),
            missing_rate=missing_rate,
            core_field_missing=core_field_missing,
        )
        confidence = mt.compute_confidence(
            missing_rate=missing_rate,
            fallback_used=bool(fallback_used),
            core_field_missing=core_field_missing,
        )

        # ── 7. 组装输出 ───────────────────────────────────────────────
        evidence = [ev_static, ev_shift, ev_hum, ev_rain, up_txt, down_txt, lifetime_model["note"]]

        result: dict[str, Any] = {
            # ── 身份标识 ──────────────────────────────────────────────
            "task_id":             task_id,
            "station_id":          station_id,
            "station_name":        task.get("station_name"),
            "start_time":          start_time,
            "end_time":            end_time,
            "lat":                 float(lat),
            "lon":                 float(lon),

            # ── 综合风险（核心冻结字段）──────────────────────────────
            "weather_risk":        float(round(weather_risk, 4)),
            "risk_type":           str(risk_type),

            # ── 四维评分（核心冻结字段）──────────────────────────────
            "dispersion_condition":            str(disp),
            "static_wind_score":               float(round(static_score, 4)),
            "wind_shift_score":                float(round(shift_score, 4)),
            "humidity_risk_score":             float(round(hum_score, 4)),
            "rain_removal_score":              float(round(rain_score, 4)),
            "insufficient_rain_removal_score": float(round(insuf_rain_score, 4)),

            # ── 主导风向（聚合，核心冻结字段）───────────────────────
            "mean_wind_speed":               agg["mean_wind_speed"],
            "dominant_wind_direction":       agg["dominant_wind_direction"],
            "dominant_wind_direction_label": agg["dominant_wind_direction_label"],
            "upwind_direction":              agg["upwind_direction"],
            "downwind_direction":            agg["downwind_direction"],

            # ── 其他聚合统计（核心冻结字段）──────────────────────────
            "mean_humidity":       agg["mean_humidity"],
            "total_precipitation": agg["total_precipitation"],
            "mean_temperature":    agg["mean_temperature"],
            "mean_pressure_hpa":   _safe_float(pd.to_numeric(hourly_df["pressure_hpa"], errors="coerce").mean()),
            "mean_cloud_cover":    agg["mean_cloud_cover"],

            # ── 天气代码（核心冻结字段）──────────────────────────────
            # 单日：weather_code_daily 为标量 int；多日：为 None，使用 dominant/series
            "weather_code_daily":           wc["weather_code_daily"],
            "dominant_weather_code_daily":  wc["dominant_weather_code_daily"],
            "weather_code_daily_series":    wc["weather_code_daily_series"],

            # ── 计数 ─────────────────────────────────────────────────
            "hourly_count":        int(len(hourly_df)),

            # ── 逐小时时序数据 ────────────────────────────────────────
            # 供前端绘制趋势图：风速、湿度、降雨、温度、风向变化
            "hourly_series":       hourly_series,

            # ── 预测字段（短时，筛查级）────────────────────────────────
            "wind_direction_forecast": wind_forecast,
            "pollution_path_prediction": pollution_path_prediction,

            # ── 兼容期保留字段（deprecated，将在下一主版本移除）──────
            # wind_speed / wind_direction 和 latest_* 都是末小时值；保留
            # 前者以满足既有 API 契约，建议新调用方使用窗口聚合字段。
            "wind_speed":            ws_f,
            "wind_direction":        wd_f,
            "latest_wind_speed":     ws_f,
            "latest_wind_direction": wd_f,
            # upwind / downwind：文字描述，建议改用
            #   upwind_direction / downwind_direction（数值）
            "upwind":   str(up_txt),
            "downwind": str(down_txt),
        }

        return {
            "agent_name":  AGENT_NAME,
            "status":      status,
            "result":      result,
            "evidence":    [str(e) for e in evidence],
            "confidence":  float(round(confidence, 4)),
            "data_quality": _build_data_quality(
                missing_rate, bool(fallback_used), api_error, extra_notes
            ),
            "created_at":  _now_hk_str(),
        }

    except Exception as exc:
        logger.exception("MeteorologyAgent 运行异常: %s", exc)
        return _failed_response(
            f"内部错误: {exc}",
            task_id=task_id,
        )


def batch_run(
    tasks: list[dict[str, Any]],
    rules_path: Path | None = None,
    fallback_csv: Path | None = None,
) -> list[dict[str, Any]]:
    """
    批处理入口：接受任意数量的 task 列表，逐个调用 run()，返回等长结果列表。

    - 任何单个任务失败（校验错误 / 运行异常）写入该任务的 failed 响应，不影响其他任务
    - 返回列表与输入列表等长、顺序一致
    """
    if not isinstance(tasks, list):
        raise TypeError(f"batch_run 期望 list[dict]，实际收到 {type(tasks).__name__}")

    results: list[dict[str, Any]] = []
    for idx, task in enumerate(tasks):
        task_id = task.get("task_id", f"batch_{idx}")
        try:
            result = run(task, rules_path=rules_path, fallback_csv=fallback_csv)
        except Exception as exc:
            logger.exception("batch_run 第 %d 条任务异常 task_id=%s: %s", idx, task_id, exc)
            result = _failed_response(f"批处理内部错误: {exc}", task_id=task_id)

        # 确保 result 字典存在（failed 时 result={} 符合契约，无需补 task_id）
        results.append(result)

    return results
