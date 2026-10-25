"""气象数据 ETL：将 API/CSV 原始数据整理为统一小时级表结构。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from config.field_schema import (  # 字段定义唯一来源
    CORE_FIELDS,
    RAW_TO_STANDARD,
    STANDARD_COLUMNS,
)


def _hour_floor(series: pd.Series) -> pd.Series:
    """时间统一到小时。"""
    return pd.to_datetime(series).dt.floor("h")


def raw_to_standard_hourly(
    raw_df: pd.DataFrame,
    station_id: str,
    lat: float,
    lon: float,
    location_id: str | None = None,
) -> pd.DataFrame:
    """
    将 Open-Meteo 原始 DataFrame 转为统一小时级结构。
    字段映射由 config/field_schema.json 集中管理。
    缺失值保留行，并在 is_missing 中标记各核心字段是否缺失。
    """
    if raw_df.empty:
        out = pd.DataFrame(columns=STANDARD_COLUMNS)
        return out

    loc_id = location_id or f"{station_id}_{lat:.4f}_{lon:.4f}"
    df = raw_df.copy()
    df["time"] = _hour_floor(df["time"])

    # 原始列名 → 标准列名（来自 field_schema.RAW_TO_STANDARD）
    for src, dst in RAW_TO_STANDARD.items():
        if src in df.columns:
            df[dst] = df[src]
        elif dst not in df.columns:
            df[dst] = np.nan

    # 确保所有标准列都存在（schema 新增字段时自动补 NaN）
    for col in STANDARD_COLUMNS:
        if col not in ("time", "station_id", "location_id", "lat", "lon", "is_missing"):
            if col not in df.columns:
                df[col] = np.nan

    df["station_id"] = station_id
    df["location_id"] = loc_id
    df["lat"] = float(lat)
    df["lon"] = float(lon)

    # 缺失标记（核心字段列表来自 field_schema.CORE_FIELDS）
    missing_flags = []
    for _, row in df.iterrows():
        flags = {f: bool(pd.isna(row.get(f))) for f in CORE_FIELDS}
        missing_flags.append(flags)
    df["is_missing"] = missing_flags

    # 只保留 schema 定义的标准列（自动跳过 df 中不存在的列）
    valid_cols = [c for c in STANDARD_COLUMNS if c in df.columns]
    out = df[valid_cols].copy()

    # 确保 JSON 可序列化：时间转字符串、NaN → None
    out["time"] = out["time"].astype(str)
    nullable_cols = [c for c in CORE_FIELDS + [
        col for col in STANDARD_COLUMNS
        if col not in CORE_FIELDS + ["time", "station_id", "location_id", "lat", "lon", "is_missing"]
    ] if c in out.columns]
    for col in nullable_cols:
        out[col] = out[col].where(pd.notna(out[col]), None)

    return out


def dataframe_records_for_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    """将标准 DataFrame 转为可 JSON 序列化的记录列表（用于调试或扩展输出）。"""
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rec = {}
        for k, v in row.items():
            if isinstance(v, (np.floating, float)) and (pd.isna(v) if not isinstance(v, float) else False):
                rec[k] = None
            elif isinstance(v, float) and np.isnan(v):
                rec[k] = None
            else:
                rec[k] = v
        records.append(rec)
    return records
