"""
Coordinator HTTP 层：一次请求跑完整条分析链。

链路与 `agents/coordinator_min.py` 完全一致（不经过 LLM 调度，纯确定性编排）：

    AirQuality → Meteorology → Traffic → Spatial → Source → Decision → Report

返回体就是前端 `types/analysis.ts` 里的 `CoordinatorResponse`，
七个 Agent 的结果 + `pipeline_status`，字段与 `examples/day9_coordinator_output.json` 相同。

**每一块都来自本项目的真实数据源**，没有任何示例数据：

| 块 | 数据来源 |
|---|---|
| air_quality | `data/air_quality.db`（EPD EPICDI 逐小时多污染物浓度） |
| meteorology | Open-Meteo Archive API（`/api/meteorology/analyze` 同一条链路） |
| traffic     | `data/traffic.db`（运输署 ATC 交通统计的车型构成） |
| spatial     | `spatial_agent/data/`（路网中心线 + 敏感 POI + spatial.db） |
| source / decision / report | 由上面四块推导 |
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.coordinator_min import CoordinatorMin  # noqa: E402

logger = logging.getLogger(__name__)

#: meta 统计的进程内缓存。这些数字（库行数、geojson 要素数）在运行期不变，
#: 没必要每次请求都重数 106 万行。
_META_CACHE: dict[str, Any] | None = None


def _analysis_meta() -> dict[str, Any]:
    """收集加载浮层要展示的**真实**统计数。

    浮层里每一行"正在用什么数据"都必须来自这里，而不是前端写死的文案 ——
    数据更新后行数会变，写死会变成假信息。
    """
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE

    import json  # noqa: PLC0415
    import sqlite3  # noqa: PLC0415

    meta: dict[str, Any] = {}

    # ① 空气质量库（EPD EPICDI 逐小时 + AQHI）
    try:
        conn = sqlite3.connect(str(ROOT / "data" / "air_quality.db"))
        total = conn.execute("SELECT COUNT(*) FROM air_quality_hourly").fetchone()[0]
        with_aqhi = conn.execute(
            "SELECT COUNT(*) FROM air_quality_hourly WHERE aqhi IS NOT NULL"
        ).fetchone()[0]
        meta["epd_hourly_rows"] = total
        meta["epd_station_count"] = conn.execute(
            "SELECT COUNT(*) FROM air_quality_stations"
        ).fetchone()[0]
        meta["aqhi_coverage"] = round(with_aqhi / total, 4) if total else 0.0
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取空气质量库统计失败：%s", exc)

    # ② 交通库（运输署 ATC 车型构成）
    try:
        conn = sqlite3.connect(str(ROOT / "data" / "traffic.db"))
        meta["atc_mix_rows"] = conn.execute(
            "SELECT COUNT(*) FROM traffic_vehicle_mix"
        ).fetchone()[0]
        meta["atc_station_count"] = conn.execute(
            "SELECT COUNT(DISTINCT atc_station_id) FROM traffic_vehicle_mix"
        ).fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取交通库统计失败：%s", exc)

    # ③ 空间数据（路网中心线 + 敏感 POI）
    for key, path in (
        ("road_segments", ROOT / "spatial_agent" / "data" / "processed" / "road_network.geojson"),
        ("sensitive_poi", ROOT / "spatial_agent" / "data" / "processed" / "poi_sensitive.geojson"),
    ):
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                meta[key] = len(data.get("features") or [])
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取 %s 失败：%s", path.name, exc)

    # ④ 空间检索半径（SpatialAgent.run 的默认参数，就是链路里实际用的值）
    meta["spatial_radius_km"] = 3.0

    # ⑤ 出行稳健性扰动设置（travel_agent 实际读取的 config.uncertainty）
    try:
        from tools.travel_advice_tools import load_travel_config  # noqa: PLC0415

        uncertainty = (load_travel_config().get("uncertainty") or {})
        meta["travel_robustness"] = {
            "samples": int(uncertainty.get("samples") or 0),
            "wind_speed_rel_sigma": float(uncertainty.get("wind_speed_rel_sigma") or 0.15),
            "wind_direction_sigma_deg": float(
                uncertainty.get("wind_direction_sigma_deg") or 20.0
            ),
            "exchange_rel_sigma": float(uncertainty.get("exchange_rel_sigma") or 0.2),
        }
    except Exception as exc:  # noqa: BLE001 - 配置读不到就不展示这块
        logger.warning("读取出行稳健性配置失败：%s", exc)

    # ⑥ 暴露场网格粒度（pollution_rules.grid.cell_km）
    try:
        rules = json.loads(
            (ROOT / "config" / "pollution_rules.json").read_text(encoding="utf-8")
        )
        meta["exposure_grid_cell_km"] = float(rules["grid"]["cell_km"])
        meta["exposure_min_index"] = float(rules["grid"]["min_index"])
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        logger.warning("读取污染场网格配置失败：%s", exc)

    _META_CACHE = meta
    return meta


def _jsonable(value: Any) -> Any:
    """把 numpy 标量/数组转成 JSON 能序列化的原生类型。

    各 Agent 内部用了 numpy，`round(np.float64)` 之类的结果直接放进 jsonify
    会抛 "Object of type float64 is not JSON serializable"。
    """
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    # numpy 标量都有 item()，转成对应的 Python 原生类型
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except Exception:  # noqa: BLE001 - 转不动就退回字符串，不让接口 500
            return str(value)
    return str(value)


def _build_task(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """从请求体整理出任务字典，返回 (task, 错误信息)。"""
    task = {
        "task_id": body.get("task_id") or "",
        "station_name": (body.get("station_name") or "").strip(),
        "station_id": (body.get("station_id") or "").strip(),
        "start_time": (body.get("start_time") or "").strip(),
        "end_time": (body.get("end_time") or "").strip(),
        "lat": body.get("lat"),
        "lon": body.get("lon"),
    }

    missing = [k for k in ("station_name", "start_time", "end_time") if not task[k]]
    if missing:
        return task, f'缺少必填字段: {", ".join(missing)}'

    if not task["task_id"]:
        stamp = task["start_time"].replace("-", "").replace(":", "").replace(" ", "_")
        task["task_id"] = f"{task['station_id'] or task['station_name']}_{stamp}"

    # 站点坐标可以从登记表补，不强制要求调用方给
    if task["lat"] is None or task["lon"] is None:
        coordinate = _lookup_station_coordinate(task["station_name"], task["station_id"])
        if coordinate is not None:
            task["lat"], task["lon"] = coordinate
    return task, None


def _lookup_station_coordinate(station_name: str, station_id: str) -> tuple[float, float] | None:
    """从监测站登记表（stations.csv + station_mapping.json）里找坐标。"""
    import csv  # noqa: PLC0415 - 仅此接口使用
    import json  # noqa: PLC0415

    csv_path = ROOT / "spatial_agent" / "data" / "stations.csv"
    wanted = station_name.replace(" Roadside", "").strip().lower()

    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                names = {
                    (row.get("station_name") or "").strip().lower(),
                    (row.get("station_name_zh") or "").strip().lower(),
                    (row.get("station_id") or "").strip().lower(),
                }
                if wanted in names or station_id.lower() in names:
                    try:
                        return float(row["lat"]), float(row["lon"])
                    except (TypeError, ValueError, KeyError):
                        break

    mapping_path = ROOT / "data" / "station_mapping.json"
    if mapping_path.is_file():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        for name, info in (mapping.get("air_quality_to_atc") or {}).items():
            if name.strip().lower() == wanted:
                try:
                    return float(info["lat"]), float(info["lon"])
                except (TypeError, ValueError, KeyError):
                    break
    return None


def create_blueprint(coordinator: CoordinatorMin | None = None) -> Blueprint:
    """建蓝图。传入 coordinator 便于测试时注入替身，正常路径用默认实例。"""
    engine = coordinator or CoordinatorMin()
    blueprint = Blueprint("analysis_agent", __name__)

    @blueprint.get("/health")
    def health() -> Any:
        return jsonify({"agent_name": "CoordinatorMin", "status": "ok"})

    @blueprint.get("/analyze/meta")
    def analysis_meta() -> Any:
        """各数据源的真实统计数，供前端加载浮层展示"正在用什么"。"""
        return jsonify(_analysis_meta())

    @blueprint.post("/analyze")
    def analyze() -> Any:
        """跑完整链路，返回与前端 CoordinatorResponse 一致的整包结果。"""
        body: dict[str, Any] = request.get_json(silent=True) or {}
        task, error = _build_task(body)
        if error:
            return jsonify({"status": "error", "message": error}), 400

        try:
            result = engine.analyze(task)
        except Exception as exc:  # noqa: BLE001 - 任何一个 Agent 崩了都要给出结构化错误
            logger.exception("Coordinator 分析失败")
            return jsonify({"status": "error", "message": f"分析失败: {exc}"}), 500

        return jsonify(_jsonable(result))

    return blueprint
