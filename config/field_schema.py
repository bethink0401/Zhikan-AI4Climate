"""
数据契约层：从 field_schema.json 加载字段映射，供 ETL 和 API 客户端使用。

所有字段定义的唯一来源是 field_schema.json，本模块只做解析和导出，
不包含任何业务逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).resolve().parent / "field_schema.json"


def _load() -> dict[str, Any]:
    with _SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_schema = _load()

# ── API 固定请求参数 ─────────────────────────────────────────────────────────
# open_meteo_client 的唯一参数来源，不得在函数内硬编码
_params = _schema["api_request_params"]
API_TIMEZONE: str = _params["timezone"]                    # "Asia/Hong_Kong"
API_WIND_SPEED_UNIT: str = _params["wind_speed_unit"]      # "ms"
API_TEMPERATURE_UNIT: str = _params["temperature_unit"]    # "celsius"
API_PRECIPITATION_UNIT: str = _params["precipitation_unit"] # "mm"

# ── API 请求字段 ────────────────────────────────────────────────────────────
# open_meteo_client.HOURLY_VARIABLES 的唯一来源
HOURLY_VARIABLES: list[str] = _schema["api_request_fields"]["fields"]

# open_meteo_client.DAILY_VARIABLES 的唯一来源
DAILY_VARIABLES: list[str] = _schema["api_daily_fields"]["fields"]

# ── 原始列名 → 标准列名 映射 ────────────────────────────────────────────────
# ETL rename_map 的唯一来源；key=原始列名，value=标准列名
RAW_TO_STANDARD: dict[str, str] = {
    k: v
    for k, v in _schema["raw_to_standard"].items()
    if not k.startswith("_")
}

# ── ETL 标准输出列顺序 ───────────────────────────────────────────────────────
# ETL STANDARD_COLUMNS 的唯一来源
STANDARD_COLUMNS: list[str] = _schema["standard_columns"]["columns"]

# ── 核心字段（参与缺失标记）──────────────────────────────────────────────────
CORE_FIELDS: list[str] = _schema["core_fields"]["fields"]

# ── 各评分维度使用的字段 ────────────────────────────────────────────────────
SCORING_FIELDS: dict[str, list[str]] = _schema["scoring_fields"]

# ── 已保留字段（评分未用，但输出保留）──────────────────────────────────────
RETAINED_FIELDS: list[str] = _schema["retained_fields"]["fields"]

# ── 未来扩展字段（已抓取，尚未使用）────────────────────────────────────────
FUTURE_FIELDS: list[str] = _schema["future_fields"]["fields"]
