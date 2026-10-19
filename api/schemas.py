"""
Web 层请求/响应结构说明。

不引入第三方验证库，只用标准 dataclass 描述期望结构，
供路由函数做文档注释和轻量校验参考。
实际字段契约以 docs/api_contract.md 和 agents/meteorology_agent.validate_task 为准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── POST /api/meteorology/analyze ──────────────────────────────────────────

@dataclass
class AnalyzeRequest:
    """
    单任务或批处理请求体。

    单任务：直接传一个 task 对象
      {"task_id": "...", "lat": 22.3, "lon": 114.1,
       "start_time": "...", "end_time": "..."}

    批处理：传 tasks 列表
      {"tasks": [{...}, {...}]}

    两者互斥，同时存在时优先走批处理。
    """
    # 单任务必填（对应 validate_task 的必填字段）
    task_id: str | None = None       # 必填
    lat: float | None = None         # 必填
    lon: float | None = None         # 必填
    start_time: str | None = None    # 必填
    end_time: str | None = None      # 必填
    # 单任务可选
    station_id: str | None = None
    station_name: str | None = None
    # 批处理
    tasks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AnalyzeResponse:
    """
    POST /analyze 响应体。

    mode="single"  → result 为单个 agent JSON
    mode="batch"   → result 为 list[agent JSON]
    """
    mode: str          # "single" | "batch"
    count: int         # 任务总数
    result: Any        # dict | list[dict]


# ── GET /api/meteorology/hourly ────────────────────────────────────────────

@dataclass
class HourlyRequest:
    """
    GET /hourly 查询参数。

    必填：lat, lon, start_time, end_time
    可选：station_id
    时区固定为 Asia/Hong_Kong，不接受外部 timezone 参数。
    """
    lat: float
    lon: float
    start_time: str
    end_time: str
    station_id: str = "UNKNOWN"


@dataclass
class HourlyResponse:
    """
    GET /hourly 响应体。
    """
    station_id: str
    lat: float
    lon: float
    start_time: str
    end_time: str
    hourly_count: int
    fallback_used: bool
    records: list[dict[str, Any]]   # 标准小时级记录列表
