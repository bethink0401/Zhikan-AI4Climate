"""Weather-aware, habit-aware travel advice.

This module owns the "judgement" half of the Travel Agent: it reads the recorded
meteorology and pollution signals, translates them into travel impact factors,
and scores candidate routes against one user's travel-habit profile.

All thresholds and weights live in ``config/travel_profile.json``; nothing here
hardcodes a numeric limit.

Scope note
----------
Scores are screening-level and explainable. They rank candidate routes under
given weather and personal sensitivity, and are not a physiological comfort
model, a live traffic prediction, or a substitute for official transport
advisories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spatial_agent.tools.spatial_tools import haversine_km
from tools.route_tools import bearing_deg, bearing_label

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "travel_profile.json"

#: 出行条件等级 → 中文标签，供前端与报告统一展示。
CONDITION_LABELS = {
    "good": "适宜出行",
    "fair": "基本适宜",
    "poor": "需谨慎出行",
    "severe": "不建议长时间户外出行",
}

#: 出行方式 → 中文标签。
MODE_LABELS = {
    "walk": "步行",
    "drive": "驾车",
    "transit": "公共交通",
}

#: 分段类型 → 中文标签（公共交通由四段合成，见 config 的 segments 节）。
DEFAULT_SEGMENT_LABELS = {
    "walk": "步行",
    "drive": "驾车",
    "transit_wait": "候车与进出站",
    "transit_rail": "轨道区间",
}

#: 各因子在"出行条件总分"中的权重（缺省值，配置可覆盖）。
DEFAULT_CONDITION_WEIGHTS = {
    "rain": 0.28,
    "heat": 0.22,
    "humidity": 0.16,
    "wind": 0.12,
    "air_quality": 0.22,
}

#: 通勤式速度折减：各天气因子对有效速度的最大折减比例（缺省值，配置可覆盖）。
DEFAULT_SPEED_LOSS_CAP = {
    "rain": 0.30,
    "heat": 0.20,
    "humidity": 0.12,
    "wind": 0.10,
    "air_quality": 0.05,
}

#: 各分段的呼吸速率（L/min）。步行按中等体力活动取 22，车内静坐约 10。
DEFAULT_VENTILATION_LPM = {
    "walk": 22.0,
    "drive": 10.0,
    "transit_wait": 12.0,
    "transit_rail": 10.0,
}

#: 各分段相对街面浓度的一次穿透比例：步行 1.0，关窗车厢约 0.35，站厅 0.6，区间 0.1。
DEFAULT_PENETRATION = {
    "walk": 1.0,
    "drive": 0.35,
    "transit_wait": 0.6,
    "transit_rail": 0.1,
}

#: 各分段对天气的隔绝程度，用于"户外等效时间"。
DEFAULT_ENCLOSURE = {
    "walk": 1.0,
    "drive": 0.6,
    "transit_wait": 0.5,
    "transit_rail": 0.1,
}

#: 默认单次路口等待时长（秒）。步行取 18 s（随机到达信号路口的平均等待），
#: 车辆另含排队与起步损失，取 10 s。
DEFAULT_JUNCTION_WAIT_SECONDS = {"walk": 18.0, "drive": 10.0}

#: 默认驾车道路等级速度系数与高峰拥堵系数。
DEFAULT_DRIVE_CLASS_FACTOR = {"trunk": 1.3, "local": 0.9}
DEFAULT_CONGESTION = {
    "peak_hours": [7, 8, 9, 17, 18, 19],
    "peak_factor": 0.7,
    "offpeak_factor": 1.0,
}

#: 默认轨道运行参数。
DEFAULT_RAIL = {
    "speed_kmh": 32.0,
    "dwell_seconds": 45.0,
    "platform_access_minutes": 4.0,
    "wait_minutes": 3.0,
}


def _numeric_overrides(section: Any) -> dict[str, float]:
    """Keep only the numeric entries of a config section.

    The JSON config carries ``_comment`` keys for documentation; those are not
    values and would break the arithmetic if they leaked into a weight table.
    """
    if not isinstance(section, dict):
        return {}
    return {
        key: float(value)
        for key, value in section.items()
        if not str(key).startswith("_") and isinstance(value, (int, float))
    }


def scoring_config(config: dict[str, Any]) -> dict[str, Any]:
    """Merge the ``scoring`` block of the config over the module defaults."""
    scoring = config.get("scoring") or {}
    return {
        "condition_weights": {
            **DEFAULT_CONDITION_WEIGHTS, **_numeric_overrides(scoring.get("condition_weights"))
        },
        "speed_loss_cap": {
            **DEFAULT_SPEED_LOSS_CAP, **_numeric_overrides(scoring.get("speed_loss_cap"))
        },
        "max_total_speed_loss": float(scoring.get("max_total_speed_loss", 0.45)),
        "score_anchor_minutes": float(scoring.get("score_anchor_minutes", 40.0)),
        "walk_overrun_minutes_per_km": float(
            scoring.get("walk_overrun_minutes_per_km", 25.0)
        ),
    }


def segments_config(config: dict[str, Any]) -> dict[str, Any]:
    """Reading of the ``segments`` / ``intake`` blocks (呼吸速率、穿透、隔绝)."""
    segments = config.get("segments") or {}
    intake = config.get("intake") or {}
    return {
        "labels": {**DEFAULT_SEGMENT_LABELS, **(segments.get("labels") or {})},
        "reference_ventilation_lpm": float(
            intake.get("reference_ventilation_lpm", 22.0)
        ),
        "ventilation_lpm": {**DEFAULT_VENTILATION_LPM, **_numeric_overrides(intake.get("ventilation_lpm"))},
        "penetration": {**DEFAULT_PENETRATION, **_numeric_overrides(intake.get("penetration"))},
        "enclosure": {**DEFAULT_ENCLOSURE, **_numeric_overrides(intake.get("enclosure"))},
    }


def time_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Reading of the ``time_model`` block (路口等待、道路等级、拥堵、轨道参数)."""
    model = config.get("time_model") or {}
    congestion = {**DEFAULT_CONGESTION, **(model.get("congestion") or {})}
    rail = {**DEFAULT_RAIL, **_numeric_overrides(model.get("rail"))}
    turn_wait = _numeric_overrides(model.get("junction_wait_seconds")) or dict(
        DEFAULT_JUNCTION_WAIT_SECONDS
    )
    return {
        "junction_wait_seconds": turn_wait,
        "drive_class_speed_factor": _numeric_overrides(
            model.get("drive_class_speed_factor")
        )
        or dict(DEFAULT_DRIVE_CLASS_FACTOR),
        "congestion": {
            "peak_hours": {int(hour) for hour in congestion["peak_hours"]},
            "peak_factor": float(congestion["peak_factor"]),
            "offpeak_factor": float(congestion["offpeak_factor"]),
        },
        "rail": rail,
        "max_transit_access_km": float(model.get("max_transit_access_km", 1.2)),
    }


def load_travel_config(path: str | Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    """Load the travel profile configuration."""
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def list_profiles(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the available travel-habit profiles."""
    return list(config.get("profiles") or [])


def resolve_profile(
    config: dict[str, Any], profile: str | dict[str, Any] | None
) -> dict[str, Any]:
    """Resolve a profile id, an inline profile dict, or None into a profile dict."""
    profiles = list_profiles(config)
    if isinstance(profile, dict):
        merged = dict(profiles[0]) if profiles else {}
        merged.update(profile)
        return merged
    if profile:
        wanted = str(profile).strip().lower()
        for candidate in profiles:
            if candidate.get("profile_id", "").lower() == wanted:
                return candidate
        for candidate in profiles:
            if wanted in str(candidate.get("profile_name", "")).lower():
                return candidate
    if not profiles:
        raise ValueError("travel_profile.json 中没有任何画像定义")
    return profiles[0]


# ── 气象 → 影响因子 ─────────────────────────────────────────────────────────

def _ratio(value: float, low: float, high: float) -> float:
    """Linear ramp from 0 at ``low`` to 1 at ``high``, clamped."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def extract_weather_summary(
    meteorology_result: dict[str, Any] | None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pull the travel-relevant weather fields out of a Meteorology Agent response."""
    payload = meteorology_result or {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    source = result if isinstance(result, dict) and result else (fallback or {})

    hourly = source.get("hourly_series") or []
    peak_precipitation = 0.0
    peak_wind_gust = 0.0
    max_temperature = None
    for hour in hourly:
        try:
            peak_precipitation = max(peak_precipitation, float(hour.get("precipitation") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            peak_wind_gust = max(peak_wind_gust, float(hour.get("wind_gusts") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            temperature = float(hour.get("temperature"))
            max_temperature = temperature if max_temperature is None else max(max_temperature, temperature)
        except (TypeError, ValueError):
            pass

    hourly_count = source.get("hourly_count") or len(hourly) or 1
    total_precipitation = source.get("total_precipitation")
    mean_precipitation = None
    if total_precipitation is not None and hourly_count:
        try:
            mean_precipitation = float(total_precipitation) / float(hourly_count)
        except (TypeError, ValueError, ZeroDivisionError):
            mean_precipitation = None

    def number(key: str) -> float | None:
        value = source.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "available": bool(source),
        "weather_risk": number("weather_risk"),
        "risk_type": source.get("risk_type"),
        "dispersion_condition": source.get("dispersion_condition"),
        "mean_wind_speed": number("mean_wind_speed"),
        "mean_humidity": number("mean_humidity"),
        "mean_temperature": number("mean_temperature") or max_temperature,
        "mean_pressure_hpa": number("mean_pressure_hpa"),
        "total_precipitation": total_precipitation,
        "peak_hourly_precipitation": round(peak_precipitation, 3),
        "mean_hourly_precipitation": round(mean_precipitation, 3) if mean_precipitation is not None else None,
        "peak_wind_gust": round(peak_wind_gust, 3) if peak_wind_gust else None,
        "dominant_wind_direction": number("dominant_wind_direction"),
        "dominant_wind_direction_label": source.get("dominant_wind_direction_label"),
        "downwind_direction": number("downwind_direction"),
        "upwind_direction": number("upwind_direction"),
        "hourly_count": source.get("hourly_count"),
        "hourly_series": hourly,
    }


def classify_weather(
    weather: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    pollution_risk: float | None = None,
) -> dict[str, Any]:
    """Turn raw weather numbers into per-factor severity values in 0..1.

    Each factor is scored independently so the advice can say *why* a trip is
    uncomfortable rather than only that it is.
    """
    rain_thresholds = thresholds["rain_mm_per_hour"]
    temperature_thresholds = thresholds["temperature_c"]
    humidity_thresholds = thresholds["humidity_pct"]
    wind_thresholds = thresholds["wind_speed_ms"]
    air_thresholds = thresholds["air_quality_risk"]

    # 采用时段内峰值小时降雨，因为出行者感受到的是"出门那一刻下多大"。
    rain_value = weather.get("peak_hourly_precipitation")
    if rain_value is None:
        rain_value = weather.get("mean_hourly_precipitation") or 0.0
    rain = _ratio(
        float(rain_value),
        float(rain_thresholds["none"]),
        float(rain_thresholds["heavy"]),
    )

    temperature = weather.get("mean_temperature")
    heat = 0.0
    cold = 0.0
    if temperature is not None:
        heat = _ratio(
            float(temperature),
            float(temperature_thresholds["comfort_high"]),
            float(temperature_thresholds["very_hot"]),
        )
        cold = _ratio(
            float(temperature_thresholds["comfort_low"]) - float(temperature),
            0.0,
            float(temperature_thresholds["comfort_low"]) - float(temperature_thresholds["cold"]),
        )

    humidity_value = weather.get("mean_humidity")
    humidity = 0.0
    if humidity_value is not None:
        humidity = _ratio(
            float(humidity_value),
            float(humidity_thresholds["comfort"]),
            float(humidity_thresholds["very_muggy"]),
        )

    wind_value = weather.get("mean_wind_speed")
    wind = 0.0
    if wind_value is not None:
        wind = _ratio(
            float(wind_value),
            float(wind_thresholds["breezy"]),
            float(wind_thresholds["gale"]),
        )
    gust_value = weather.get("peak_wind_gust")
    if gust_value is not None:
        gust = _ratio(
            float(gust_value),
            float(thresholds["wind_gust_ms"]["noticeable"]),
            float(thresholds["wind_gust_ms"]["strong"]),
        )
        wind = max(wind, gust)

    air_quality = 0.0
    if pollution_risk is not None:
        air_quality = _ratio(
            float(pollution_risk),
            float(air_thresholds["good"]),
            float(air_thresholds["very_poor"]),
        )

    return {
        "rain": round(rain, 3),
        "heat": round(heat, 3),
        "cold": round(cold, 3),
        "humidity": round(humidity, 3),
        "wind": round(wind, 3),
        "air_quality": round(air_quality, 3),
        "pollution_risk_input": pollution_risk,
        "rain_mm_per_hour_used": rain_value,
    }


def personalized_severity(
    severity: dict[str, float], profile: dict[str, Any], config: dict[str, Any]
) -> dict[str, float]:
    """Scale each factor by how much this traveller actually minds it."""
    sensitivities = profile.get("sensitivities") or {}
    scaled: dict[str, float] = {}
    for factor in ("rain", "heat", "cold", "humidity", "wind", "air_quality"):
        value = float(severity.get(factor) or 0.0)
        weight = float(sensitivities.get(factor, 1.0))
        scaled[factor] = round(max(0.0, min(1.0, value * weight)), 3)
    return scaled


def overall_condition(
    scaled: dict[str, float], config: dict[str, Any]
) -> dict[str, Any]:
    """Combine the personalized factors into one trip-level condition score."""
    weights = scoring_config(config)["condition_weights"]
    total_weight = sum(weights.values())
    impact = sum(
        weights[factor] * float(scaled.get(factor) or 0.0)
        for factor in weights
    ) / total_weight
    score = round((1.0 - impact) * 100.0, 1)

    if score >= 80.0:
        level = "good"
    elif score >= 60.0:
        level = "fair"
    elif score >= 40.0:
        level = "poor"
    else:
        level = "severe"

    return {
        "score": score,
        "level": level,
        "level_label": CONDITION_LABELS[level],
        "impact": round(impact, 3),
        "dominant_factors": [
            factor
            for factor, value in sorted(scaled.items(), key=lambda item: -item[1])
            if value >= 0.4
        ][:3],
    }


# ── 天气速度折减 ────────────────────────────────────────────────────────────

def effective_speed(
    base_speed_kmh: float, scaled: dict[str, float], config: dict[str, Any]
) -> dict[str, Any]:
    """Reduce the planning speed by the personalized weather burden."""
    settings = scoring_config(config)
    caps = settings["speed_loss_cap"]
    loss = 0.0
    breakdown: dict[str, float] = {}
    for factor, cap in caps.items():
        factor_loss = cap * float(scaled.get(factor) or 0.0)
        breakdown[factor] = round(factor_loss, 4)
        loss += factor_loss
    loss = min(loss, settings["max_total_speed_loss"])
    speed = max(base_speed_kmh * (1.0 - loss), 1.5)
    return {
        "base_speed_kmh": round(base_speed_kmh, 2),
        "effective_speed_kmh": round(speed, 2),
        "speed_loss_ratio": round(loss, 3),
        "speed_loss_breakdown": breakdown,
    }


def road_minutes(
    *,
    mode: str,
    edges: list[dict[str, Any]] | None,
    distance_km: float,
    base_speed_kmh: float,
    junctions: int,
    hour: int | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    """把里程换算成耗时：计入路口等待、道路等级与时段拥堵。

    旧实现是"里程 ÷ 恒定速度"，等于宣称驾车在任何路段、任何时段都恒快同样的倍数。
    在弥敦道早高峰这不成立，而耗时直接决定方式选择，所以必须按时段与道路等级区分。
    """
    model = time_model_config(config)
    congestion = model["congestion"]
    factor = float(congestion["offpeak_factor"])
    if hour is not None and int(hour) in congestion["peak_hours"]:
        factor = float(congestion["peak_factor"])

    if mode == "drive":
        trunk_factor = float(model["drive_class_speed_factor"].get("trunk", 1.0))
        local_factor = float(model["drive_class_speed_factor"].get("local", 1.0))
        moving = 0.0
        if edges:
            for edge in edges:
                class_factor = trunk_factor if edge.get("route_number") else local_factor
                speed = max(base_speed_kmh * factor * class_factor, 1.0)
                moving += float(edge["length_km"]) / speed * 60.0
        else:
            speed = max(base_speed_kmh * factor * local_factor, 1.0)
            moving = distance_km / speed * 60.0
    else:
        moving = distance_km / max(base_speed_kmh, 0.5) * 60.0

    wait = float(model["junction_wait_seconds"].get(mode, 0.0)) * max(int(junctions), 0) / 60.0
    average_speed = distance_km / moving * 60.0 if moving > 0 else base_speed_kmh
    return {
        "moving_minutes": round(moving, 2),
        "junction_wait_minutes": round(wait, 2),
        "junctions": max(int(junctions), 0),
        "minutes": round(moving + wait, 2),
        "avg_speed_kmh": round(average_speed, 2),
        # 拥堵与道路等级只影响驾车；步行段报 1.0，避免误导。
        "congestion_factor": round(factor, 2) if mode == "drive" else 1.0,
    }


def rail_minutes(
    rail_distance_km: float, stop_count: int, config: dict[str, Any]
) -> dict[str, Any]:
    """轨道段耗时：运行时间 + 每站停站 + 进站与候车固定开销。"""
    rail = time_model_config(config)["rail"]
    running = rail_distance_km / max(float(rail["speed_kmh"]), 1.0) * 60.0
    dwell = float(rail["dwell_seconds"]) * max(int(stop_count), 0) / 60.0
    fixed = float(rail["platform_access_minutes"]) + float(rail["wait_minutes"])
    return {
        "running_minutes": round(running, 2),
        "dwell_minutes": round(dwell, 2),
        "fixed_minutes": round(fixed, 2),
        "minutes": round(running + dwell + fixed, 2),
    }


# ── 时间 / 户外等效时间 / 吸入当量 ─────────────────────────────────────────

def segments_profile(
    segments: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any]:
    """把分段列表换算成耗时、户外等效时间与吸入当量。

    ``intake_index`` 的单位 EU = "以步行呼吸量计的污染分钟数"：
    平均暴露指数 × 停留分钟 × (该段呼吸速率 ÷ 步行呼吸速率) × 该段一次穿透比例。
    这样同样是主干道上的 3 公里，步行与车内的吸入量才可比。旧的
    Σ指数×公里 口径不含停留时间与呼吸量，三种方式的"剂量"其实不是同一个物理量，
    却被直接同台比较、同台归一化。
    """
    settings = segments_config(config)
    reference = float(settings["reference_ventilation_lpm"])
    total_minutes = 0.0
    outdoor_minutes = 0.0
    intake = 0.0
    by_kind: dict[str, dict[str, float]] = {}

    for segment in segments:
        kind = str(segment.get("kind") or "walk")
        minutes = float(segment.get("minutes") or 0.0)
        index = float(segment.get("mean_index") or 0.0)
        ventilation = float(settings["ventilation_lpm"].get(kind, reference))
        penetration = float(settings["penetration"].get(kind, 1.0))
        enclosure = float(settings["enclosure"].get(kind, 1.0))

        contribution = index * minutes * (ventilation / reference) * penetration
        total_minutes += minutes
        outdoor_minutes += minutes * enclosure
        intake += contribution

        bucket = by_kind.setdefault(kind, {"minutes": 0.0, "intake_index": 0.0})
        bucket["minutes"] += minutes
        bucket["intake_index"] += contribution

    return {
        "minutes": round(total_minutes, 2),
        "outdoor_equivalent_minutes": round(outdoor_minutes, 2),
        "intake_index": round(intake, 4),
        "by_kind": {
            kind: {
                "label": settings["labels"].get(kind, kind),
                "minutes": round(values["minutes"], 2),
                "intake_index": round(values["intake_index"], 4),
            }
            for kind, values in by_kind.items()
        },
    }


def evaluate_option(
    *,
    segments: list[dict[str, Any]],
    minutes: float,
    distance_km: float,
    mode: str,
    profile: dict[str, Any],
    scaled: dict[str, float],
    config: dict[str, Any],
) -> dict[str, Any]:
    """把一条候选方案折算成一个可比较的效用值（单位：分钟）。

    评分模型的全部内容就是这一个式子::

        U = 户外等效时间 × (1 + 画像天气乘数 × 天气负担)
            + 画像交换率 × 吸入当量
            + 方式常数
            + 超限罚分（徒步超出耐受距离、超出单程时间上限）

    每一项都以"分钟"为单位，所以可加、可比较、可解释。旧的加性扣分模型把
    "走多远"拆成耗时／里程／天气暴露／超时四项，实测两两相关系数 0.94~0.99，
    等于重复计四遍；而且以候选集最大值做归一化，同一条路的得分会随
    "这次还比了哪几条"变化。效用式不含候选集相对量，因此可分解、可复现。
    """
    settings = scoring_config(config)
    segment_stats = segments_profile(segments, config)

    weather_load = sum(
        float(scaled.get(factor) or 0.0)
        for factor in ("rain", "heat", "humidity", "wind")
    ) / 4.0
    weather_multiplier = float(profile.get("weather_time_multiplier", 0.8))
    weather_minutes = (
        segment_stats["outdoor_equivalent_minutes"] * weather_multiplier * weather_load
    )

    exchange = float(profile.get("pollution_exchange_minutes", 2.0))
    pollution_minutes = exchange * segment_stats["intake_index"]

    mode_constant = float((profile.get("mode_constant_minutes") or {}).get(mode, 0.0))

    max_trip_minutes = float(profile.get("max_trip_minutes") or 999.0)
    overrun_minutes = max(0.0, minutes - max_trip_minutes)

    max_walk_km = float(profile.get("max_walk_km") or 999.0)
    walk_overrun_km = max(0.0, distance_km - max_walk_km) if mode == "walk" else 0.0
    walk_overrun_minutes = walk_overrun_km * settings["walk_overrun_minutes_per_km"]

    utility = (
        segment_stats["outdoor_equivalent_minutes"]
        + weather_minutes
        + pollution_minutes
        + mode_constant
        + overrun_minutes
        + walk_overrun_minutes
    )

    # 展示用得分：对效用做软饱和映射，单调且不会因量程差异把整类方案压成 0。
    anchor = max(float(settings["score_anchor_minutes"]), 1.0)
    score = round(100.0 * anchor / (anchor + max(utility, 0.0)), 1)

    return {
        "score": score,
        "utility_minutes": round(utility, 2),
        "utility_detail": {
            "outdoor_equivalent_minutes": segment_stats["outdoor_equivalent_minutes"],
            "weather_added_minutes": round(weather_minutes, 2),
            "pollution_added_minutes": round(pollution_minutes, 2),
            "mode_constant_minutes": round(mode_constant, 2),
            "overrun_minutes": round(overrun_minutes, 2),
            "walk_overrun_minutes": round(walk_overrun_minutes, 2),
        },
        "intake_detail": segment_stats,
        "weather_load": round(weather_load, 3),
        "weather_time_multiplier": weather_multiplier,
        "exchange_minutes_per_eu": exchange,
        "within_max_walk": mode != "walk" or distance_km <= max_walk_km,
        "within_max_trip_minutes": minutes <= max_trip_minutes,
    }


def profile_time_pressure(profile: dict[str, Any], config: dict[str, Any] | None = None
                          ) -> float:
    """画像的时间压力 ∈ (0,1)：时间越紧 → 越接近 1。

    用于 Tchebycheff 决策器的画像驱动权重：时间压力越大，"时间"目标的权重
    越高；时间宽裕的画像（跑步、观光）更应兼顾暴露/体感。

    V9.6 修正：旧公式 `T_max/(T_max+T_flex)` 的单调性反了——固定弹性时，
    允许的最大出行时间越长，权重反而越大（"越宽裕越像时间紧迫"）。现在用

        pressure = T_ref / (T_ref + T_max + T_flex)

    对齐语义：**最大出行时间或时间弹性任一变大，时间压力都单调不增**。
    T_ref 是"典型单程时长"这一标尺（配置 decision.reference_minutes），
    决定 pressure 的量级；不对应任何单个画像，避免把某个画像当基准。
    """
    settings = decision_config(config or {})
    reference = float(settings.get("reference_minutes") or 30.0)
    max_trip = float(profile.get("max_trip_minutes") or 0.0)
    flex = float(profile.get("time_flexibility_minutes") or 0.0)
    budget = max_trip + flex
    if budget <= 0:
        return 0.5
    return reference / (reference + budget)


def decision_config(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("decision") or {}
    return {"rule": str(section.get("rule") or "weighted_sum"),
            "epsilon": float(section.get("epsilon") or 1e-9),
            "weight_basis": str(section.get("weight_basis") or "time_pressure"),
            "reference_minutes": float(section.get("reference_minutes") or 30.0),
            "fixed_time_weight": float(section.get("fixed_time_weight") or 0.5)}


def tchebycheff_pick(options: list[dict[str, Any]], profile: dict[str, Any],
                     config: dict[str, Any]) -> dict[str, Any] | None:
    """归一化 + 画像驱动的加权 Tchebycheff：按"最差目标偏差"选方案。

    与线性加权和的区别：加权和可以让"某一目标极差、另一目标极优"的候选
    胜出（两个目标的亏欠互相补偿）；Tchebycheff 取两个归一化偏差的最大值，
    因此不会用一个目标的超额收益去掩盖另一个目标的严重不足。

    两个目标均为绝对量（在途时间、吸入当量），用候选集**极差**归一化后
    与理想点（各目标最小值）比较；权重由画像时间压力导出（时间压力越大，
    时间目标权重越高）。归一化只用候选集自身的量程，不依赖"这次比了哪几条"
    之外的任何外部信息。
    """
    if not options:
        return None
    settings = decision_config(config)
    eps = settings["epsilon"]
    times = [float(o["duration_minutes"]) for o in options]
    intakes = [float(o["intake_index"]) for o in options]
    t_min, t_max = min(times), max(times)
    e_min, e_max = min(intakes), max(intakes)
    t_span = (t_max - t_min) or eps
    e_span = (e_max - e_min) or eps

    w_time, w_intake = resolve_decision_weights(profile, config)

    def distance(option: dict[str, Any]) -> float:
        d_time = (float(option["duration_minutes"]) - t_min) / t_span
        d_intake = (float(option["intake_index"]) - e_min) / e_span
        return max(w_time * d_time, w_intake * d_intake)

    return min(options, key=lambda o: (distance(o), o["utility_minutes"]))


def resolve_decision_weights(profile: dict[str, Any], config: dict[str, Any]
                             ) -> tuple[float, float]:
    """按配置的 weight_basis 解析 (w_time, w_intake)，两者和为 1。

    - ``fixed``            : 固定 0.5/0.5（不区分画像，作为消融对照）；
    - ``time_pressure_legacy``: 旧公式 T_max/(T_max+T_flex)（单调性有误，仅对照）；
    - ``time_pressure``（默认）: V9.6 语义修正后的 T_ref/(T_ref+T_max+T_flex)。
    """
    settings = decision_config(config)
    basis = settings["weight_basis"]
    if basis == "fixed":
        w_time = settings["fixed_time_weight"]
    elif basis == "time_pressure_legacy":
        max_trip = float(profile.get("max_trip_minutes") or 0.0)
        flex = float(profile.get("time_flexibility_minutes") or 0.0)
        total = max_trip + flex
        w_time = (max_trip / total) if (max_trip > 0 and total > 0) else 0.5
    else:
        w_time = profile_time_pressure(profile, config)
    w_time = max(0.0, min(1.0, w_time))
    return w_time, 1.0 - w_time


def select_recommendation(options: list[dict[str, Any]], profile: dict[str, Any],
                          config: dict[str, Any]) -> dict[str, Any] | None:
    """按配置的决策规则从候选里挑推荐方案（Pareto 非支配筛选先行）。

    两种规则都只在不被支配的候选里选——推荐一个被严格支配的劣选无法自洽。
    规则仅决定"在非支配前沿上如何取舍"：加权和取效用最优，Tchebycheff 取
    归一化最差偏差最小者。
    """
    if not options:
        return None
    nondominated = [o for o in options if not o.get("pareto_dominated")]
    pool = nondominated or options
    rule = decision_config(config)["rule"]
    if rule == "tchebycheff":
        picked = tchebycheff_pick(pool, profile, config)
        if picked is not None:
            return picked
    return min(pool, key=lambda o: (o["utility_minutes"], o["duration_minutes"]))


# ── 出行建议 ────────────────────────────────────────────────────────────────

def build_gear_reminders(
    weather: dict[str, Any], scaled: dict[str, float], mode: str
) -> list[str]:
    """Practical items to bring, derived from the personalized factors."""
    reminders: list[str] = []
    if scaled.get("rain", 0.0) >= 0.35:
        reminders.append("携带雨具；路面湿滑，预留额外步行时间")
    if scaled.get("heat", 0.0) >= 0.4:
        reminders.append("高温时段注意补水与防晒，尽量走有遮荫的一侧")
    if scaled.get("humidity", 0.0) >= 0.5:
        reminders.append("湿度偏高体感闷热，建议选择透气衣物并放慢步行节奏")
    if scaled.get("wind", 0.0) >= 0.4:
        reminders.append("风力偏大，避免撑伞骑行或在高楼间风口长时间停留")
    if scaled.get("air_quality", 0.0) >= 0.4:
        reminders.append("空气质量较差，敏感人群建议佩戴口罩并缩短户外停留")
    if mode == "walk" and float(weather.get("mean_temperature") or 0) >= 30.0:
        reminders.append("优先选择有遮挡的骑楼与林荫路段，避免长时间暴晒")
    return reminders


def build_departure_advice(
    weather: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Suggest whether to leave now, wait, or bring the trip forward."""
    hourly = weather.get("hourly_series") or []
    flexibility = int(profile.get("time_flexibility_minutes") or 0)
    base = {
        "time_flexibility_minutes": flexibility,
        "recommended_window": None,
        "avoid_window": None,
        "note": "",
    }

    if not hourly:
        base["note"] = "缺少逐小时气象序列，无法给出出发时段建议。"
        return base

    thresholds = config["weather_thresholds"]
    rain_light = float(thresholds["rain_mm_per_hour"]["light"])
    temperature_hot = float(thresholds["temperature_c"]["hot"])

    scored: list[tuple[str, float, float]] = []
    for hour in hourly:
        try:
            precipitation = float(hour.get("precipitation") or 0.0)
        except (TypeError, ValueError):
            precipitation = 0.0
        try:
            temperature = float(hour.get("temperature"))
        except (TypeError, ValueError):
            temperature = 0.0
        try:
            humidity = float(hour.get("humidity") or 0.0)
        except (TypeError, ValueError):
            humidity = 0.0
        burden = (
            0.6 * _ratio(precipitation, 0.0, rain_light)
            + 0.3 * _ratio(temperature, temperature_hot, temperature_hot + 4.0)
            + 0.1 * _ratio(humidity, float(thresholds["humidity_pct"]["comfort"]), 100.0)
        )
        scored.append((str(hour.get("time")), round(burden, 3), precipitation))

    best = min(scored, key=lambda item: item[1])
    worst = max(scored, key=lambda item: item[1])

    def short(time_text: str) -> str:
        return time_text.split(" ")[-1][:5] if " " in time_text else time_text[:5]

    base["recommended_window"] = f"{short(best[0])} 前后"
    base["avoid_window"] = f"{short(worst[0])} 前后" if worst[1] - best[1] >= 0.15 else None

    if flexibility >= 30 and worst[1] - best[1] >= 0.15:
        base["note"] = (
            f"分析时段内 {short(best[0])} 出行负担最低（{best[1]:.2f}），"
            f"建议避开 {short(worst[0])}（{worst[1]:.2f}）。"
        )
    elif worst[1] - best[1] < 0.15:
        base["note"] = "分析时段内气象条件变化不大，按原计划出发即可。"
    else:
        base["note"] = (
            f"分析时段内 {short(best[0])} 出行负担最低（{best[1]:.2f}），"
            "但该画像时间弹性有限，建议维持既定出发时间并做好防护。"
        )
    base["hourly_burden"] = [
        {"time": time_text, "burden": burden} for time_text, burden, _ in scored
    ]
    return base


#: 效用各项 → 中文说明，用于"主要劣势"这类解释。
UTILITY_COMPONENT_LABELS = {
    "outdoor_equivalent_minutes": "户外等效时间长",
    "weather_added_minutes": "天气把体感时间拉长",
    "pollution_added_minutes": "吸入当量高",
    "mode_constant_minutes": "该出行方式本身不合偏好",
    "overrun_minutes": "超出该画像可接受的单程时间",
    "walk_overrun_minutes": "超出该画像的步行耐受距离",
}


def build_route_reason(
    route: dict[str, Any],
    mode: str,
    profile: dict[str, Any],
    evaluation: dict[str, Any],
    recommended: bool,
) -> str:
    """One-sentence explanation of why this route scored the way it did."""
    mode_label = MODE_LABELS.get(mode, mode)
    detail = evaluation["utility_detail"]
    parts: list[str] = []

    if recommended:
        parts.append(
            f"{mode_label}方案在该出行习惯下等效时间最短"
            f"（{evaluation['utility_minutes']:.0f} 分钟）"
        )
    else:
        weakest = max(detail.items(), key=lambda item: item[1])[0]
        label = UTILITY_COMPONENT_LABELS.get(weakest, weakest)
        parts.append(f"主要劣势：{label}（+{detail[weakest]:.0f} 等效分钟）")

    if route.get("road_names"):
        parts.append("主干路径：" + " → ".join(route["road_names"][:3]))

    intake = evaluation["intake_detail"]
    parts.append(
        f"吸入当量 {intake['intake_index']:.2f} EU"
        f"（户外等效 {intake['outdoor_equivalent_minutes']:.0f} 分钟）"
    )
    return "；".join(parts) + "。"


def wind_note(weather: dict[str, Any]) -> str:
    """Describe which way the wind pushes pollution, for route-side guidance."""
    downwind = weather.get("downwind_direction")
    if downwind is None:
        return "风向数据不可用，无法判断污染物传输方向。"
    label = weather.get("dominant_wind_direction_label") or bearing_label(
        float(weather.get("dominant_wind_direction") or 0.0)
    )
    return (
        f"主导风向 {label}（来风），污染物向下风向 {bearing_label(float(downwind))} "
        f"（{float(downwind):.0f}°）传输，建议优先选择上风向一侧的通勤路径。"
    )


def build_pollution_advice(
    *,
    field_summary: dict[str, Any],
    route_options: list[dict[str, Any]],
    recommended: dict[str, Any],
    origin: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Concrete "how to avoid the pollution" guidance for one trip.

    Everything here is derived from the exposure field and the route metrics that
    were actually computed, so the advice stays consistent with the ranking.
    """
    notes: list[str] = []
    # 没有暴露场时**不能用空结论冒充结论**：否则"未建立"会被读成"无需避污"。
    if not field_summary:
        return {
            "available": False,
            "headline": "本次未建立污染暴露场，无法给出避污建议",
            "hotspot": {},
            "avoidance": {},
            "cleanest_route_id": None,
            "cleanest_route_is_recommended": False,
            "notes": [
                "缺少建立暴露场所需的风速／风向或污染源位置；"
                "这不代表沿线没有污染，只是本次没有可用的污染信息。"
            ],
        }

    peak = field_summary.get("peak") or {}
    transport_bearing = field_summary.get("transport_bearing")
    calm = bool(field_summary.get("calm"))

    # 起点本身的暴露水平，决定"能不能靠绕行避开"。
    origin_exposure = 0.0
    for option in route_options:
        origin_exposure = max(
            origin_exposure, float(option.get("exposure", {}).get("peak_index") or 0.0)
        )

    hotspot_advice: dict[str, Any] = {"peak": peak, "calm": calm}
    if peak and peak.get("index"):
        distance_km = haversine_km(origin["lon"], origin["lat"], peak["lon"], peak["lat"])
        bearing = bearing_deg(origin["lon"], origin["lat"], peak["lon"], peak["lat"])
        hotspot_advice.update(
            {
                "distance_from_origin_km": round(distance_km, 3),
                "bearing_from_origin": round(bearing, 1),
                "direction_label": bearing_label(bearing),
            }
        )
        notes.append(
            f"暴露最高的位置在起点{bearing_label(bearing)}方向约 {distance_km:.2f} km 处"
            f"（相对指数 {float(peak['index']):.2f}）。"
        )

    if calm:
        notes.append("当前为静风，污染物不向下游输送而就地积聚，绕行到更远的街道收益有限。")
    elif transport_bearing is not None:
        notes.append(
            f"扩散方向为 {bearing_label(float(transport_bearing))}"
            f"（{float(transport_bearing):.0f}°）；与该方向垂直的街道暴露更低，"
            "沿污染源的上风向一侧通行更划算。"
        )

    # 推荐路线相对"最短路线"的避污收益。
    shortest = min(route_options, key=lambda item: item["distance_km"]) if route_options else None
    recommended_exposure = recommended.get("exposure") or {}
    avoidance: dict[str, Any] = {
        "dose_reduction_pct": 0.0,
        "high_exposure_km_avoided": 0.0,
        "extra_distance_km": 0.0,
        "extra_minutes": 0.0,
        "avoided_high_exposure_fully": False,
    }
    best_exposure_route = min(
        (option for option in route_options),
        key=lambda item: (
            float(item.get("exposure", {}).get("dose_index") or 0.0),
            float(item.get("exposure", {}).get("high_exposure_km") or 0.0),
        ),
        default=None,
    )

    if shortest is not None and shortest["route_id"] != recommended["route_id"]:
        baseline = shortest.get("exposure") or {}
        base_dose = float(baseline.get("dose_index") or 0.0)
        base_high = float(baseline.get("high_exposure_km") or 0.0)
        rec_dose = float(recommended_exposure.get("dose_index") or 0.0)
        rec_high = float(recommended_exposure.get("high_exposure_km") or 0.0)
        avoidance.update(
            {
                "dose_reduction_pct": round((base_dose - rec_dose) / base_dose * 100.0, 1)
                if base_dose > 0
                else 0.0,
                "high_exposure_km_avoided": round(max(0.0, base_high - rec_high), 3),
                "extra_distance_km": round(
                    recommended["distance_km"] - shortest["distance_km"], 3
                ),
                "extra_minutes": round(
                    recommended["duration_minutes"] - shortest["duration_minutes"], 1
                ),
                "avoided_high_exposure_fully": rec_high <= 0.0 < base_high,
            }
        )
        if avoidance["avoided_high_exposure_fully"]:
            notes.append(
                f"推荐方案完全绕开了高暴露路段（最短路线有 {base_high:.2f} km 处于高暴露区），"
                f"代价是多走 {avoidance['extra_distance_km']:.2f} km、"
                f"多花 {avoidance['extra_minutes']:.0f} 分钟。"
            )
        elif avoidance["dose_reduction_pct"] > 3.0:
            notes.append(
                f"推荐方案比最短路线的吸入剂量代理低 {avoidance['dose_reduction_pct']:.0f}%，"
                f"多走 {avoidance['extra_distance_km']:.2f} km。"
            )
    elif shortest is not None:
        notes.append("该行程的最短路线同时就是暴露最低的路线，无需为避污绕行。")

    # 若起点就在高暴露区内，任何路线都要先穿出去，必须如实说明。
    origin_peak = float(recommended_exposure.get("peak_index") or 0.0)
    if origin_peak >= float(config["avoidance"]["high_exposure_threshold"]):
        notes.append(
            f"注意：起点附近即处于高暴露区（途经路段峰值指数 {origin_peak:.2f}），"
            "绕行只能减少停留里程，无法完全避开；建议缩短户外停留并做好防护。"
        )

    headline = "避污建议"
    if best_exposure_route is not None:
        best_exposure = best_exposure_route.get("exposure") or {}
        headline = (
            f"本次比较中暴露最低的是「{best_exposure_route['road_names'][0] if best_exposure_route['road_names'] else best_exposure_route['mode_label']}」"
            f"（均暴露 {float(best_exposure.get('mean_index') or 0):.3f}，"
            f"高暴露里程 {float(best_exposure.get('high_exposure_km') or 0):.2f} km）"
        )

    return {
        "available": True,
        "headline": headline,
        "hotspot": hotspot_advice,
        "avoidance": avoidance,
        "cleanest_route_id": best_exposure_route["route_id"] if best_exposure_route else None,
        "cleanest_route_is_recommended": bool(
            best_exposure_route and best_exposure_route["route_id"] == recommended["route_id"]
        ),
        "notes": notes,
    }


__all__ = [
    "CONDITION_LABELS",
    "MODE_LABELS",
    "UTILITY_COMPONENT_LABELS",
    "build_departure_advice",
    "build_gear_reminders",
    "build_pollution_advice",
    "build_route_reason",
    "classify_weather",
    "effective_speed",
    "evaluate_option",
    "extract_weather_summary",
    "list_profiles",
    "load_travel_config",
    "overall_condition",
    "personalized_severity",
    "rail_minutes",
    "resolve_profile",
    "road_minutes",
    "scoring_config",
    "segments_config",
    "segments_profile",
    "time_model_config",
    "wind_note",
]
