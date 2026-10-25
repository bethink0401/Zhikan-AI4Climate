"""Wind-vector forecasting and physically interpretable pollution trajectories.

Wind directions in this project use the meteorological *wind-from* convention.
The functions below keep that convention at the boundary and only convert to a
wind-to bearing while advecting a parcel.  They deliberately do not use a
black-box model: with only a short, station-level hourly history, a weighted
circular persistence forecast is more honest and can report its uncertainty.
"""

from __future__ import annotations

import math
import json
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from tools.parcel_lifetime import load_parcel_rules, species_survival

EARTH_RADIUS_KM = 6371.0088
MIN_DIRECTIONAL_WIND_MS = 0.5
# 扩散半角上限（度）。扇形边界按 tan(半角) 展开，超过 90° 会折返，
# 且 70°+ 的半角会给出几百公里的"预期扩散"，对小时级预警没有意义。
MAX_SPREAD_HALF_ANGLE_DEG = 55.0
# 扩散半宽上限（km）。短时预警的可用尺度是公里级，几百公里的扇形
# 既不可信也会在地图上糊成一大片。
MAX_SPREAD_HALF_WIDTH_KM = 25.0
# 锥体起点的人工混合半宽（km），避免扇形从零宽开始变成尖刺。
INITIAL_MIXING_HALF_WIDTH_KM = 0.5
# 湍流横向扩散系数（km / sqrt(km)），随行程开方增长，模拟烟羽缓慢展宽。
TURBULENT_SPREAD_COEFF = 0.15


def _signed_angular_difference(a: float, b: float) -> float:
    """Smallest signed rotation from bearing ``a`` to bearing ``b``."""
    return (b - a + 180.0) % 360.0 - 180.0


def _circular_mean(degrees: np.ndarray, weights: np.ndarray) -> tuple[float | None, float]:
    if not len(degrees) or float(weights.sum()) <= 0:
        return None, 0.0
    radians = np.deg2rad(degrees)
    x = float(np.average(np.cos(radians), weights=weights))
    y = float(np.average(np.sin(radians), weights=weights))
    resultant = min(1.0, math.hypot(x, y))
    if resultant < 1e-12:
        return None, 0.0
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0, resultant


def destination_point(lon: float, lat: float, distance_km: float, bearing_deg: float) -> list[float]:
    """Return the WGS84 point reached from ``lon, lat`` along a true bearing."""
    delta = distance_km / EARTH_RADIUS_KM
    bearing = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta)
        + math.cos(phi1) * math.sin(delta) * math.cos(bearing)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(bearing) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return [math.degrees(lambda2), math.degrees(phi2)]


def forecast_wind_vectors(
    hourly: pd.DataFrame,
    horizon_hours: int = 3,
    lookback_hours: int = 12,
) -> dict[str, Any]:
    """Forecast hourly wind with recency-weighted circular persistence.

    A circular mean avoids the 0/360-degree discontinuity.  The latest half of
    the window is compared with the earlier half to extrapolate a bounded turn
    rate.  Directional concentration (resultant length) determines uncertainty
    and whether a path is safe to publish.
    """
    horizon_hours = max(1, min(int(horizon_hours), load_parcel_rules()["max_horizon_hours"]))
    data = hourly.tail(max(2, int(lookback_hours))).copy()
    direction = pd.to_numeric(data.get("wind_direction", pd.Series(index=data.index, dtype=float)), errors="coerce")
    speed = pd.to_numeric(data.get("wind_speed", pd.Series(index=data.index, dtype=float)), errors="coerce")
    valid = direction.notna() & speed.notna() & (speed >= 0)
    direction = direction[valid].to_numpy(dtype=float) % 360.0
    speed = speed[valid].to_numpy(dtype=float)
    if not len(direction):
        return {
            "status": "unavailable",
            "method": "recency_weighted_circular_persistence",
            "reason": "起算时间之前没有有效风速和风向记录",
            "forecast_hours": [],
            "confidence": 0.0,
        }

    # Recent observations get exponentially higher weight; zero wind does not
    # establish a transport direction, so it receives only a small weight.
    recency = np.exp(np.linspace(-1.4, 0.0, len(direction)))
    weights = recency * np.maximum(speed, MIN_DIRECTIONAL_WIND_MS)
    base_from, resultant = _circular_mean(direction, weights)
    if base_from is None or resultant < 0.2:
        return {
            "status": "unavailable",
            "method": "recency_weighted_circular_persistence",
            "reason": "风向离散度过高，无法形成稳定的主导输送方向",
            "forecast_hours": [],
            "confidence": round(resultant, 4),
        }

    split = len(direction) // 2
    older, _ = _circular_mean(direction[:split], weights[:split])
    newer, _ = _circular_mean(direction[split:], weights[split:])
    turn_rate = 0.0 if older is None or newer is None else _signed_angular_difference(older, newer) / max(1, len(direction) - split)
    turn_rate = max(-20.0, min(20.0, turn_rate))
    speed_forecast = float(np.average(speed, weights=recency))
    # Circular standard deviation, increasing with forecast lead time.
    base_uncertainty = min(90.0, math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(resultant, 1e-8))))))
    if len(direction) == 1:
        base_uncertainty = MAX_SPREAD_HALF_ANGLE_DEG
    confidence = max(0.0, min(1.0, resultant * min(1.0, len(direction) / 6.0)))
    forecasts: list[dict[str, float | int]] = []
    for lead in range(1, horizon_hours + 1):
        wind_from = (base_from + turn_rate * lead) % 360.0
        forecasts.append({
            "lead_hour": lead,
            "wind_from_direction": round(wind_from, 2),
            "wind_to_direction": round((wind_from + 180.0) % 360.0, 2),
            "wind_speed_ms": round(speed_forecast, 3),
            # 航向不确定度的物理上限就是 90°（超过它边界会折返、tan 变号）。
            # 这里先钳到 MAX_SPREAD_HALF_ANGLE_DEG，避免下游按 tan 展开时失控。
            "direction_uncertainty_deg": round(
                min(MAX_SPREAD_HALF_ANGLE_DEG, base_uncertainty + abs(turn_rate) * lead), 2
            ),
        })
    return {
        "status": "ok" if speed_forecast >= MIN_DIRECTIONAL_WIND_MS else "calm",
        "method": "recency_weighted_circular_persistence",
        "input_note": ("起算时仅有一条风观测，按持续情景模拟，无法估计风向变化"
                       if len(direction) == 1 else "仅使用起算时间及之前的风观测"),
        "initial_wind_from_direction": round(float(direction[-1]), 2),
        "initial_wind_speed_ms": round(float(speed[-1]), 3),
        "lookback_hours_used": int(len(direction)),
        "resultant_length": round(resultant, 4),
        "turn_rate_deg_per_hour": round(turn_rate, 3),
        "confidence": round(confidence, 4),
        "forecast_hours": forecasts,
    }


def predict_pollution_path(
    lon: float,
    lat: float,
    wind_forecast: dict[str, Any],
    *,
    lifetime_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advect one notional pollutant parcel with a temporal predictor-corrector step.

    This is a screening-level Lagrangian trajectory, not a concentration model:
    Each pollutant's life follows the shared thesis Eq. (3-5) model. Missing
    concentration inputs leave survival unavailable, not an invented half-life.
    """
    forecasts = wind_forecast.get("forecast_hours", [])
    if wind_forecast.get("status") != "ok" or not forecasts:
        return {
            "status": "unavailable",
            "method": "predictor_corrector_lagrangian_advection",
            "reason": wind_forecast.get("reason", "弱风或不稳定风向，无法预测定向传播路径"),
            "path_geojson": None,
            "uncertainty_corridor_geojson": None,
            "steps": [],
            "lifetime_model": dict(lifetime_model or {}),
        }

    current_lon, current_lat = float(lon), float(lat)
    coordinates = [[current_lon, current_lat]]
    left_edge: list[list[float]] = [[current_lon, current_lat]]
    right_edge: list[list[float]] = [[current_lon, current_lat]]
    steps: list[dict[str, Any]] = []
    lifetime_model = dict(lifetime_model or {})
    active = {name: item for name, item in (lifetime_model.get("pollutants") or {}).items()
              if float(item.get("initial_concentration") or 0) > 0}
    fractions = {name: 1.0 for name in active}
    cutoff = float(lifetime_model.get("termination_fraction", 0.01))
    termination_reason = "forecast_horizon_reached"
    previous_bearing = (float(wind_forecast.get("initial_wind_from_direction",
                   (float(forecasts[0]["wind_to_direction"]) + 180.0) % 360.0)) + 180.0) % 360.0
    previous_speed = float(wind_forecast.get("initial_wind_speed_ms", forecasts[0]["wind_speed_ms"]))
    for forecast in forecasts:
        speed = float(forecast["wind_speed_ms"])
        end_bearing = float(forecast["wind_to_direction"])
        # Thesis APTN-style temporal predictor/corrector: project with the
        # starting vector, then correct using the next-hour forecast vector.
        # No spatial interpolation is claimed because this input has one site.
        start_rad, end_rad = map(math.radians, (previous_bearing, end_bearing))
        east = (previous_speed * math.sin(start_rad) + speed * math.sin(end_rad)) / 2
        north = (previous_speed * math.cos(start_rad) + speed * math.cos(end_rad)) / 2
        integrated_speed = math.hypot(east, north)
        bearing = math.degrees(math.atan2(east, north)) % 360.0 if integrated_speed else end_bearing
        distance_km = integrated_speed * 3.6  # m/s × one hour
        hourly_survival = species_survival(lifetime_model, integrated_speed)
        fractions = {name: fraction * hourly_survival[name] for name, fraction in fractions.items()}
        current_lon, current_lat = destination_point(current_lon, current_lat, distance_km, bearing)
        previous_bearing, previous_speed = end_bearing, speed
        coordinates.append([current_lon, current_lat])
        lead = int(forecast["lead_hour"])
        uncertainty = min(MAX_SPREAD_HALF_ANGLE_DEG, float(forecast["direction_uncertainty_deg"]))
        # 锥体半宽 = 初始混合宽度 + 角扩散 + 湍流横向扩散，并钳在上限内。
        # 旧写法 travelled * tan(uncertainty) 在 70°+ 时给出几百公里，
        # 既超出短时预警的公里级尺度，也会让地图糊成一大片。
        travelled_km = sum(float(s["distance_this_hour_km"]) for s in steps) + distance_km
        angular_km = travelled_km * math.tan(math.radians(uncertainty))
        turbulent_km = TURBULENT_SPREAD_COEFF * math.sqrt(max(travelled_km, 0.0))
        width_km = min(
            MAX_SPREAD_HALF_WIDTH_KM,
            INITIAL_MIXING_HALF_WIDTH_KM + angular_km * 0.5 + turbulent_km,
        )
        left_edge.append(destination_point(current_lon, current_lat, width_km, bearing - 90.0))
        right_edge.append(destination_point(current_lon, current_lat, width_km, bearing + 90.0))
        steps.append({
            "lead_hour": lead,
            "lon": round(current_lon, 7),
            "lat": round(current_lat, 7),
            "wind_to_direction": round(bearing, 2),
            "distance_this_hour_km": round(distance_km, 3),
            "distance_from_origin_km": round(travelled_km, 3),
            "remaining_fraction": round(max(fractions.values()), 4) if fractions else None,
            "remaining_percent": round(max(fractions.values()) * 100, 2) if fractions else None,
            "remaining_by_pollutant": {
                name: {"remaining_fraction": round(fraction, 6),
                       "remaining_percent": round(fraction * 100, 2),
                       "remaining_life": round(float(active[name]["initial_concentration"]) * fraction, 6),
                       "unit": active[name]["unit"],
                       "active": fraction > cutoff}
                for name, fraction in fractions.items()
            },
            "corridor_half_width_km": round(width_km, 3),
        })
        if fractions and all(value <= cutoff for value in fractions.values()):
            termination_reason = "all_parcels_below_threshold"
            break
    corridor = [*left_edge, *reversed(right_edge), left_edge[0]]
    sectors, sector_union = propagation_sectors(float(lon), float(lat), steps)
    return {
        "status": "ok",
        "method": "predictor_corrector_lagrangian_advection",
        "lifetime_model": lifetime_model,
        "remaining_fraction_semantics": "最慢衰减污染物的气团剩余比例；不同污染物浓度不相加",
        "termination_reason": termination_reason,
        "assumptions": [
            "风向按气象学来向输入，并在平流时转换为去向",
            "每段对起止小时风矢量进行预测-校正积分；当前为单站风场，未做空间插值",
            ("温度、湿度、气压和各污染物浓度通过论文式(3-5)更新寿命，未来环境因子按起算观测持续"
             if active else "无有效污染物初值，仅计算风场路径，气团寿命未运行"),
            "修正系数按既有暴露模型参考半衰期匹配，尚未经本模型实测标定",
            "未包含地形、化学反应、降雨清除或垂直混合",
            "缺少污染物观测时仅提供风场路径，不输出剩余比例",
            "remaining_fraction 为最慢衰减污染物的气团存活包络，不是环境浓度预测",
            str(lifetime_model.get("note") or "未提供气团寿命数据"),
        ],
        "path_geojson": {"type": "LineString", "coordinates": coordinates},
        "uncertainty_corridor_geojson": {"type": "Polygon", "coordinates": [corridor]},
        "propagation_sector_geojson": sector_union,
        "sector_steps": sectors,
        "steps": steps,
    }


def propagation_sectors(lon: float, lat: float, steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Union of cumulative radial sectors, shared by map and station coverage.

    These are explicit screening envelopes based on the simulated endpoints
    and corridor widths. No client-side jitter or invented plume geometry.
    """
    from shapely.geometry import Polygon, mapping
    from shapely.ops import unary_union

    features: list[dict[str, Any]] = []
    polygons = []
    for step in steps:
        phi1, phi2 = map(math.radians, (lat, float(step["lat"])))
        delta_lon = math.radians(float(step["lon"]) - lon)
        delta_lat = phi2 - phi1
        hav = math.sin(delta_lat / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2) ** 2
        radius = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1, max(0, hav))))
        if radius < 1e-6:
            continue
        bearing = math.degrees(math.atan2(math.sin(delta_lon) * math.cos(phi2),
            math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon))) % 360
        half_angle = min(80.0, math.degrees(math.atan2(float(step["corridor_half_width_km"]), radius)))
        ring = [[lon, lat]] + [destination_point(lon, lat, radius,
            bearing - half_angle + 2 * half_angle * index / 48) for index in range(49)] + [[lon, lat]]
        polygon = Polygon(ring)
        polygons.append(polygon)
        features.append({"lead_hour": step["lead_hour"],
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
    union = json.loads(json.dumps(mapping(unary_union(polygons)))) if polygons else None
    return features, union
