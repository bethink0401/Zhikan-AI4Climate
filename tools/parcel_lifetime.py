"""Shared paper-inspired parcel survival for trajectories and route exposure.

Thesis Eq. (3-5): L_next = L * (1 - lambda * v*T*C/(RH*P) * dt).
The thesis does not supply lambda or its unit convention. We explicitly use
kelvin, percent RH, hPa, native per-pollutant concentration, and hours. Lambda
is reference-matched to the existing exposure model's configured half-life;
this is an engineering initialization, not a fit to observations.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def load_parcel_rules() -> dict[str, Any]:
    return json.loads((ROOT / "config/propagation_rules.json").read_text(encoding="utf-8"))


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def parcel_initial_conditions(
    rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Initialize all species at the same observed hour; never replace null by 0.

    Reference concentrations are positive medians of past observations, limited
    by the initialization timestamp. This prevents future observations leaking
    into a forecast, and prevents CO's different unit from dominating a sum.
    """
    rules = load_parcel_rules()
    observed = sorted((row for row in rows if row.get("time")), key=lambda row: str(row["time"]))
    if not observed:
        return {"status": "unavailable", "reason": "缺少污染物小时观测", "pollutants": {}}
    latest = observed[-1]
    stamp = str(latest["time"])
    history = [row for row in (reference_rows or observed) if str(row.get("time") or "") <= stamp]
    pollutants: dict[str, Any] = {}
    for name in rules["pollutants"]:
        initial = finite_number(latest.get(name))
        if initial is None or initial < 0:
            continue
        values = [value for row in history
                  if (value := finite_number(row.get(name))) is not None and value > 0]
        baseline = float(median(values)) if values else initial
        pollutants[name] = {
            "initial_concentration": initial,
            "reference_concentration": baseline,
            "reference_sample_count": len(values),
            "unit": rules["units"].get(name, rules["units"]["default"]),
        }
    return {"status": "ok" if pollutants else "unavailable", "observed_at": stamp,
            "reference_method": "past_positive_median",
            "reference_lookback_days": rules["reference_lookback_days"], "pollutants": pollutants}


def build_lifetime_model(
    weather: Mapping[str, Any],
    initial_conditions: Mapping[str, Any] | None,
    *,
    reference_half_life_hours: float | None = None,
) -> dict[str, Any]:
    rules = load_parcel_rules()
    reference = rules["reference_weather"]
    if reference_half_life_hours is None:
        exposure = json.loads((ROOT / "config/pollution_rules.json").read_text(encoding="utf-8"))
        reference_half_life_hours = float(exposure["plume"]["decay_half_life_hours"])
    if not math.isfinite(reference_half_life_hours) or reference_half_life_hours <= 0:
        raise ValueError("reference half-life must be positive and finite")
    # A one-hour factor matching the pre-existing reference half-life exactly.
    reference_loss = 1 - 0.5 ** (1 / reference_half_life_hours)
    missing: list[str] = []

    def measured(key: str, low: float, high: float) -> float | None:
        value = finite_number(weather.get(key))
        if value is None or not low <= value <= high:
            missing.append(key)
            return None
        return value

    temperature = measured("temperature", -90, 65)
    humidity = measured("humidity", 0.01, 100)
    pressure = measured("pressure_hpa", 100, 1100)
    temperature_k = temperature + 273.15 if temperature is not None else reference["temperature_k"]
    rh = humidity if humidity is not None else reference["humidity_pct"]
    p = pressure if pressure is not None else reference["pressure_hpa"]
    initial_conditions = initial_conditions or {}
    species: dict[str, Any] = {}
    for name, item in (initial_conditions.get("pollutants") or {}).items():
        if name not in rules["pollutants"] or not isinstance(item, Mapping):
            continue
        concentration = finite_number(item.get("initial_concentration"))
        baseline = finite_number(item.get("reference_concentration"))
        if concentration is None or concentration < 0 or baseline is None or baseline <= 0:
            continue
        coefficient = (reference_loss * reference["humidity_pct"] * reference["pressure_hpa"]
                       / (reference["wind_speed_ms"] * reference["temperature_k"] * baseline))
        species[name] = {
            **dict(item), "correction_coefficient": coefficient,
            "hourly_loss_per_ms": coefficient * temperature_k * concentration / (rh * p),
        }
    return {
        "version": rules["version"],
        "status": ("partial" if missing else "ok") if species else "unavailable",
        "method": "paper_eq_3_5_reference_matched",
        "formula": "L_next=L*max(0,1-lambda*v*T*C/(RH*P)*dt_hours)",
        "observed_at": initial_conditions.get("observed_at"),
        "concentration_reference_method": initial_conditions.get("reference_method"),
        "reference_lookback_days": initial_conditions.get("reference_lookback_days", rules["reference_lookback_days"]),
        "unavailable_reason": initial_conditions.get("reason") if not species else None,
        "weather_inputs": {"temperature": temperature, "humidity": humidity, "pressure_hpa": pressure},
        "assumed_inputs": missing,
        "reference_weather": dict(reference),
        "reference_half_life_hours": reference_half_life_hours,
        "calibration_status": rules["calibration_status"],
        "termination_fraction": rules["termination_fraction"],
        "pollutants": species,
        "note": ("按论文寿命公式计算；温度使用 K、湿度使用 %、气压使用 hPa、时间使用小时。"
                 "修正系数由既有参考半衰期匹配，尚未经实测标定。"
                 "未来温湿压和环境浓度按起算时观测持续，未包含化学反应和降雨清除。"
                 + ("缺测气象因子使用参考状态，已列入 assumed_inputs。" if missing else "")
                 + ("缺少有效污染物初值，未输出寿命衰减。" if not species else "")),
    }


def species_survival(model: Mapping[str, Any], speed_ms: float) -> dict[str, float]:
    """One-hour Eq. (3-5) survival; never permit negative life or growth."""
    speed = finite_number(speed_ms)
    if speed is None or speed < 0:
        raise ValueError("wind speed must be finite and nonnegative")
    return {name: max(0.0, min(1.0, 1 - float(item["hourly_loss_per_ms"]) * speed))
            for name, item in (model.get("pollutants") or {}).items()
            if float(item.get("initial_concentration") or 0) > 0}


def exposure_decay_rate(model: Mapping[str, Any] | None, speed_ms: float) -> float | None:
    """Continuous equivalent of hourly survival, shared by both field solvers.

    Use the slowest surviving species for a conservative relative exposure
    envelope; do not sum concentrations with incompatible pollutant units.
    """
    survival = species_survival(model or {}, speed_ms)
    return -math.log(max(max(survival.values()), 1e-12)) if survival else None
