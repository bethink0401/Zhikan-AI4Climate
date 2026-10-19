"""Open-Meteo Archive API 客户端：拉取小时级历史气象数据（含 daily weather_code）。

语义约定（与 config/field_schema.json api_request_params 节保持一致）：
  - 时区：固定 Asia/Hong_Kong（UTC+8），不使用 auto
  - 风速：m/s（wind_speed_unit=ms）
  - 气温：摄氏度（temperature_unit=celsius）
  - 降水：毫米（precipitation_unit=mm）
  - 时间格式：YYYY-MM-DD HH:mm:ss，无时区标注时按 Asia/Hong_Kong 解释
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from config.field_schema import (  # 字段列表与请求参数唯一来源
    API_PRECIPITATION_UNIT,
    API_TEMPERATURE_UNIT,
    API_TIMEZONE,
    API_WIND_SPEED_UNIT,
    DAILY_VARIABLES,
    HOURLY_VARIABLES,
)

logger = logging.getLogger(__name__)

ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _parse_datetime_to_api_dates(start_time: str, end_time: str) -> tuple[str, str]:
    """将 task 时间字符串转为 API 所需的 start_date / end_date（含小时边界）。"""
    start = pd.Timestamp(start_time)
    end = pd.Timestamp(end_time)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _merge_daily_into_hourly(hourly_df: pd.DataFrame, daily_data: dict[str, Any]) -> pd.DataFrame:
    """
    将 daily 字段（weather_code 等）按日期广播到小时级 DataFrame。

    广播规则：
      - daily.time 是日期字符串列表（如 ["2023-08-15"]）
      - 对应 hourly 中同一日期的所有小时行均使用该天的值
      - 广播后的列名加 _daily 后缀（如 weather_code → weather_code_daily）
      - weather_code_daily 表示当天主导天气代码，不代表每小时独立观测值
    """
    if not daily_data or "time" not in daily_data:
        return hourly_df

    daily_df = pd.DataFrame(daily_data)
    # dt.date 剥离时区，避免带时区 datetime 与 date 比较失败
    daily_df["date"] = pd.to_datetime(daily_df["time"]).dt.date

    hourly_df = hourly_df.copy()
    hourly_df["_date"] = hourly_df["time"].dt.date

    for col in DAILY_VARIABLES:
        if col not in daily_df.columns:
            continue
        date_map = daily_df.set_index("date")[col].to_dict()
        hourly_df[f"{col}_daily"] = hourly_df["_date"].map(date_map)

    hourly_df.drop(columns=["_date"], inplace=True)
    return hourly_df


def fetch_archive_hourly(
    lat: float,
    lon: float,
    start_time: str,
    end_time: str,
    timezone: str = API_TIMEZONE,
    timeout_sec: float = 30.0,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    调用 Open-Meteo archive API，返回原始小时 DataFrame 与错误信息（成功时 error 为 None）。

    固定参数（来自 config/field_schema.json api_request_params）：
      - timezone      = Asia/Hong_Kong
      - wind_speed_unit = ms（m/s）
      - temperature_unit = celsius
      - precipitation_unit = mm
    hourly 字段来自 HOURLY_VARIABLES，daily 字段来自 DAILY_VARIABLES，
    daily 数据按日期广播合并到 hourly 行中（列名加 _daily 后缀）。
    """
    start_date, end_date = _parse_datetime_to_api_dates(start_time, end_time)
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HOURLY_VARIABLES),
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": timezone,
        "wind_speed_unit": API_WIND_SPEED_UNIT,
        "temperature_unit": API_TEMPERATURE_UNIT,
        "precipitation_unit": API_PRECIPITATION_UNIT,
    }

    try:
        response = requests.get(ARCHIVE_BASE_URL, params=params, timeout=timeout_sec)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("Open-Meteo API 请求失败: %s", exc)
        return None, str(exc)
    except ValueError as exc:
        logger.warning("Open-Meteo 响应解析失败: %s", exc)
        return None, str(exc)

    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        return None, "API 返回缺少 hourly.time 字段"

    df = pd.DataFrame(hourly)
    if df.empty:
        return None, "API 返回空数据"

    # timezone=Asia/Hong_Kong 时，API 返回的时间字符串已是本地时间（无 UTC offset）
    df["time"] = pd.to_datetime(df["time"])
    if df["time"].dt.tz is None:
        df["time"] = df["time"].dt.tz_localize(timezone)
    else:
        df["time"] = df["time"].dt.tz_convert(timezone)

    # 统一转为 UTC 做范围过滤，避免时区歧义
    start_ts = pd.Timestamp(start_time)
    end_ts = pd.Timestamp(end_time)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(timezone)
    else:
        start_ts = start_ts.tz_convert("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize(timezone)
    else:
        end_ts = end_ts.tz_convert("UTC")

    df_utc = df["time"].dt.tz_convert("UTC")
    start_utc = start_ts.tz_convert("UTC")
    end_utc = end_ts.tz_convert("UTC")
    mask = (df_utc >= start_utc.floor("h")) & (df_utc <= end_utc.floor("h"))
    df = df.loc[mask].copy()
    df.reset_index(drop=True, inplace=True)

    # 合并 daily 字段（广播到每小时）
    daily = payload.get("daily")
    if daily:
        df = _merge_daily_into_hourly(df, daily)

    return df, None


def load_fallback_csv(csv_path: Path) -> pd.DataFrame:
    """从本地 CSV 加载兜底样例数据（Open-Meteo 原始列格式）。"""
    df = pd.read_csv(csv_path)
    df["time"] = pd.to_datetime(df["time"])
    return df


def fetch_with_fallback(
    lat: float,
    lon: float,
    start_time: str,
    end_time: str,
    fallback_csv: Path,
    timezone: str = API_TIMEZONE,
) -> tuple[pd.DataFrame, bool, Optional[str]]:
    """
    优先 API，失败则使用本地 CSV。
    返回 (DataFrame, fallback_used, api_error_message)。

    timezone 参数统一使用 API_TIMEZONE（Asia/Hong_Kong），fallback 路径同样遵循。
    """
    df, err = fetch_archive_hourly(lat, lon, start_time, end_time, timezone=timezone)
    if df is not None and not df.empty:
        return df, False, None

    if not fallback_csv.is_file():
        empty = pd.DataFrame(columns=["time"] + HOURLY_VARIABLES)
        return empty, True, err or "兜底 CSV 不存在"

    fallback_df = load_fallback_csv(fallback_csv)
    # fallback CSV 时间无时区标注，按 timezone（Asia/Hong_Kong）localize
    if fallback_df["time"].dt.tz is None:
        fallback_df["time"] = fallback_df["time"].dt.tz_localize(timezone)

    start_ts = pd.Timestamp(start_time)
    end_ts = pd.Timestamp(end_time)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(timezone)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize(timezone)

    mask = (fallback_df["time"] >= start_ts.floor("h")) & (
        fallback_df["time"] <= end_ts.floor("h")
    )
    filtered = fallback_df.loc[mask].copy()
    if filtered.empty:
        filtered = fallback_df.copy()
    filtered.reset_index(drop=True, inplace=True)
    return filtered, True, err
