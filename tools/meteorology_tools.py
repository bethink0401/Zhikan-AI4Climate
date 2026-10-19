"""气象规则与评分工具：阈值从配置文件读取。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "meteorology_rules.json"


def load_rules(rules_path: Path | None = None) -> dict[str, Any]:
    """加载 meteorology_rules.json。"""
    path = rules_path or DEFAULT_RULES_PATH
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def clamp_score(value: float) -> float:
    """评分限制在 [0, 1]。"""
    return float(max(0.0, min(1.0, value)))


def circular_angle_diff_deg(a: float, b: float) -> float:
    """
    计算两风向的圆周角差（度），范围 [0, 180]。
    例如 350° 与 10° 的差为 20°，而非 340°。
    """
    diff = abs(float(a) - float(b)) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


def compute_missing_rate(df: pd.DataFrame) -> float:
    """根据 is_missing 列统计核心字段缺失比例。"""
    if df.empty or "is_missing" not in df.columns:
        return 1.0 if df.empty else 0.0

    total_cells = 0
    missing_cells = 0
    for flags in df["is_missing"]:
        if isinstance(flags, dict):
            for _, is_miss in flags.items():
                total_cells += 1
                if is_miss:
                    missing_cells += 1
    if total_cells == 0:
        return 0.0
    return round(missing_cells / total_cells, 4)


def score_static_wind(df: pd.DataFrame, rules: dict[str, Any]) -> tuple[float, str]:
    """静风积聚评分：连续低风速小时越多，分数越高。"""
    cfg = rules["static_wind"]
    threshold = cfg["speed_threshold_ms"]
    min_hours = cfg["consecutive_hours_min"]
    max_score = cfg["max_score_at_zero_wind"]

    speeds = pd.to_numeric(df.get("wind_speed"), errors="coerce")
    if speeds is None or speeds.empty:
        return 0.0, "无风速数据，静风风险无法评估"

    low = speeds.fillna(threshold + 1) <= threshold
    max_run = 0
    run = 0
    for val in low:
        if val:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0

    if max_run < min_hours:
        score = clamp_score(max_run / max(min_hours, 1) * 0.3)
        return score, f"连续静风（≤{threshold} m/s）最长 {max_run} 小时，未达 {min_hours} 小时积聚阈值"

    # 静风越强、越久，分数越高
    mean_low_speed = speeds[low].mean() if low.any() else threshold
    severity = 1.0 - min(mean_low_speed / threshold, 1.0) if threshold > 0 else 1.0
    duration_factor = min(max_run / (min_hours * 2), 1.0)
    score = clamp_score(max_score * (0.5 * severity + 0.5 * duration_factor))
    return score, (
        f"检测到连续 {max_run} 小时风速 ≤ {threshold} m/s，"
        f"平均 {mean_low_speed:.2f} m/s，利于污染物静风积聚"
    )


def score_wind_shift(df: pd.DataFrame, rules: dict[str, Any]) -> tuple[float, str]:
    """风向突变评分：相邻小时圆周角差越大，分数越高。"""
    cfg = rules["wind_shift"]
    shift_threshold = cfg["shift_threshold_deg"]
    max_at = cfg["max_score_at_180_deg"]

    dirs = pd.to_numeric(df.get("wind_direction"), errors="coerce")
    if dirs is None or len(dirs.dropna()) < 2:
        return 0.0, "风向序列不足，无法评估突变"

    max_shift = 0.0
    for i in range(1, len(dirs)):
        if pd.isna(dirs.iloc[i - 1]) or pd.isna(dirs.iloc[i]):
            continue
        shift = circular_angle_diff_deg(dirs.iloc[i - 1], dirs.iloc[i])
        max_shift = max(max_shift, shift)

    if max_shift <= shift_threshold:
        score = clamp_score(max_shift / shift_threshold * 0.4)
        return score, f"最大相邻风向变化 {max_shift:.1f}°，低于 {shift_threshold}° 突变关注线"

    score = clamp_score(max_at * min(max_shift / 180.0, 1.0))
    return score, f"相邻小时最大风向变化 {max_shift:.1f}°（圆周角），存在扩散路径突变风险"


def score_humidity(df: pd.DataFrame, rules: dict[str, Any]) -> tuple[float, str]:
    """高湿度积聚评分。"""
    cfg = rules["humidity"]
    high = cfg["high_threshold_pct"]
    min_hours = cfg["consecutive_hours_min"]
    max_score = cfg["max_score_at_100_pct"]

    hum = pd.to_numeric(df.get("humidity"), errors="coerce")
    if hum is None or hum.empty:
        return 0.0, "无湿度数据"

    above = hum.fillna(0) >= high
    max_run = 0
    run = 0
    for val in above:
        if val:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0

    peak = hum.max()
    if max_run < min_hours:
        score = clamp_score((peak - high) / (100 - high) * 0.3) if peak and peak > high else 0.0
        return score, f"最高相对湿度 {peak:.0f}%，连续高湿 {max_run} 小时"

    score = clamp_score(max_score * min(max_run / (min_hours * 2), 1.0))
    return score, f"连续 {max_run} 小时湿度 ≥ {high}%，最高 {peak:.0f}%，不利扩散"


def score_rain_removal(df: pd.DataFrame, rules: dict[str, Any]) -> tuple[float, str]:
    """
    降雨清除不足风险评分（insufficient_rain_removal_score 语义）：
    返回值越高表示降水清除不足、积聚风险越大。
      - 无雨 / 降水 < insufficient_rain_mm → 分数趋近 1.0（清除不足）
      - 降水充足                           → 分数趋近 0.0（清除良好）

    注意：调用方 agents/meteorology_agent.py 将此返回值赋给
    insufficient_rain_removal_score，并用 (1 - 返回值) 得到 rain_removal_score。
    """
    cfg = rules["rain_removal"]
    need_mm = cfg["insufficient_rain_mm"]
    lookback = cfg["lookback_hours"]
    max_dry = cfg["max_score_when_dry"]

    precip = pd.to_numeric(df.get("precipitation"), errors="coerce")
    rain = pd.to_numeric(df.get("rain"), errors="coerce")
    if precip is not None and not precip.isna().all():
        total = precip.fillna(0).sum()
    elif rain is not None:
        total = rain.fillna(0).sum()
    else:
        return max_dry, "无降水记录，视为清除不足"

    window = df.tail(min(len(df), lookback))
    if "precipitation" in window.columns:
        w_total = pd.to_numeric(window["precipitation"], errors="coerce").fillna(0).sum()
    else:
        w_total = total

    if w_total >= need_mm:
        score = clamp_score(0.1 * (need_mm / (w_total + 0.01)))
        return score, f"近 {len(window)} 小时累计降水 {w_total:.2f} mm，清除作用较好"

    score = clamp_score(max_dry * (1.0 - w_total / need_mm))
    return score, f"近 {len(window)} 小时累计降水仅 {w_total:.2f} mm（<{need_mm} mm），清除不足"


def wind_up_downstream(
    wind_direction_deg: float | None,
) -> tuple[str, str]:
    """
    简化上/下风向描述（气象学风向为来向）。
    返回 (upwind_sector, downwind_sector) 文字说明。
    """
    if wind_direction_deg is None or (isinstance(wind_direction_deg, float) and np.isnan(wind_direction_deg)):
        return "未知", "未知"
    d = float(wind_direction_deg) % 360.0
    # 来向 sector
    sectors = [
        (337.5, 360, "北"), (0, 22.5, "北"),
        (22.5, 67.5, "东北"), (67.5, 112.5, "东"),
        (112.5, 157.5, "东南"), (157.5, 202.5, "南"),
        (202.5, 247.5, "西南"), (247.5, 292.5, "西"),
        (292.5, 337.5, "西北"),
    ]
    up = "未知"
    for lo, hi, name in sectors:
        if lo <= d < hi or (lo == 337.5 and d >= lo):
            up = name
            break
    opposite = {
        "北": "南", "东北": "西南", "东": "西", "东南": "西北",
        "南": "北", "西南": "东北", "西": "东", "西北": "东南",
    }
    down = opposite.get(up, "未知")
    return f"上风向（来向）约 {up}（{d:.0f}°）", f"下风向（去向）约 {down}"


def dispersion_condition(
    wind_speed: float | None,
    rules: dict[str, Any],
) -> str:
    """扩散条件文字判定。"""
    cfg = rules["dispersion"]
    favorable = cfg["favorable_wind_min_ms"]
    unfavorable = cfg["unfavorable_wind_max_ms"]
    if wind_speed is None or (isinstance(wind_speed, float) and np.isnan(wind_speed)):
        return "未知"
    ws = float(wind_speed)
    if ws >= favorable:
        return "有利扩散"
    if ws <= unfavorable:
        return "不利扩散（静风或弱风）"
    return "一般"


def aggregate_weather_risk(
    static_wind: float,
    wind_shift: float,
    humidity: float,
    rain_removal: float,
    rules: dict[str, Any],
) -> float:
    """加权综合气象风险。"""
    w = rules["weather_risk_weights"]
    total = (
        w["static_wind_score"] * static_wind
        + w["wind_shift_score"] * wind_shift
        + w["humidity_risk_score"] * humidity
        + w["rain_removal_score"] * rain_removal
    )
    return clamp_score(total)


def risk_type_from_score(score: float, rules: dict[str, Any]) -> str:
    """根据综合分返回 risk_type。"""
    th = rules["risk_type_thresholds"]
    if score <= th["low_max"]:
        return "low"
    if score <= th["medium_max"]:
        return "medium"
    return "high"


def build_data_quality_note(
    missing_rate: float,
    fallback_used: bool,
    api_error: str | None,
    extra_notes: list[str] | None = None,
) -> str:
    """生成 data_quality.note。extra_notes 用于追加非核心字段缺失等补充说明。"""
    parts: list[str] = []
    if fallback_used:
        parts.append("已使用本地样例数据兜底")
        if api_error:
            parts.append(f"API 原因: {api_error}")
    if missing_rate == 0.0:
        parts.append("数据完整")
    elif missing_rate < 0.2:
        parts.append("少量缺失")
    else:
        parts.append("缺失较多")
    if extra_notes:
        parts.extend(extra_notes)
    return "；".join(parts) if parts else "数据完整"


def compute_confidence(
    missing_rate: float,
    fallback_used: bool,
    core_field_missing: bool = False,
) -> float:
    """
    置信度：缺失越少、未兜底越高。
    core_field_missing=True 时额外扣减（核心字段部分缺失但仍可计算）。
    """
    base = 1.0 - missing_rate * 0.5
    if fallback_used:
        base *= 0.75
    if core_field_missing:
        base *= 0.85
    return clamp_score(base)


def determine_status(
    fallback_used: bool,
    missing_rate: float,
    core_field_missing: bool = False,
) -> str:
    """
    根据数据来源和质量确定输出 status。

    规则（与 docs/api_contract.md 输出契约保持一致）：
      - 真实 API 成功且数据完整（或仅非核心字段缺失） → "success"
      - 使用 fallback CSV                             → "warning"
      - 核心字段部分缺失但仍能计算                   → "warning"
      - API 和 fallback 均失败 / 输入错误             → "failed"（由调用方直接设置）
    """
    if fallback_used:
        return "warning"
    if core_field_missing:
        return "warning"
    return "success"


def to_python_scalar(value: Any) -> Any:
    """
    将 numpy/pandas 标量转为 Python 原生类型，保证 json.dumps 可序列化。
    None / NaN → None；numpy 数值 → float/int；其余原样返回。
    """
    import numpy as np  # 局部导入，避免循环依赖
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


# ---------------------------------------------------------------------------
# 新增：聚合统计与风向标签辅助函数（v1.5.0）
# ---------------------------------------------------------------------------

_WIND_DIRECTION_LABELS = [
    (348.75, 360.0,  "N"),
    (0.0,    11.25,  "N"),
    (11.25,  33.75,  "NNE"),
    (33.75,  56.25,  "NE"),
    (56.25,  78.75,  "ENE"),
    (78.75,  101.25, "E"),
    (101.25, 123.75, "ESE"),
    (123.75, 146.25, "SE"),
    (146.25, 168.75, "SSE"),
    (168.75, 191.25, "S"),
    (191.25, 213.75, "SSW"),
    (213.75, 236.25, "SW"),
    (236.25, 258.75, "WSW"),
    (258.75, 281.25, "W"),
    (281.25, 303.75, "WNW"),
    (303.75, 326.25, "NW"),
    (326.25, 348.75, "NNW"),
]


def wind_direction_label(degrees: float | None) -> str | None:
    """
    将来风方向（°，气象学来向，0–360）转为 16 方位英文缩写。

    返回示例：0/360° → "N"，135° → "SE"，270° → "W"。
    degrees 为 None 时返回 None。
    """
    if degrees is None:
        return None
    try:
        d = float(degrees) % 360.0
    except (TypeError, ValueError):
        return None
    for lo, hi, label in _WIND_DIRECTION_LABELS:
        if lo <= d < hi:
            return label
    return "N"  # 360.0 已被 % 归零，理论不会到达此行


def dominant_wind_direction_from_series(
    directions: "pd.Series",
) -> float | None:
    """
    用圆向量（unit vector）均值法计算主导来风方向，返回 [0, 360) 的角度。

    原理：将每个角度转为单位向量后求均值，再 atan2 反算。
    该方法在方向跨越 0°/360° 边界时（如 350°、10°）仍能正确计算约 0°，
    而简单算术均值会错误返回 180°。

    - 所有值均为 NaN 时返回 None。
    - 单个有效值时直接返回该值。
    """
    valid = pd.to_numeric(directions, errors="coerce").dropna()
    if valid.empty:
        return None
    rad = np.deg2rad(valid.values.astype(float))
    mean_sin = np.sin(rad).mean()
    mean_cos = np.cos(rad).mean()
    angle = np.degrees(np.arctan2(mean_sin, mean_cos)) % 360.0
    # arctan2 结果经 % 360 后理论上 ∈ [0, 360)，但浮点精度可能产生 360.0 本身
    if angle >= 360.0:
        angle = 0.0
    return float(round(angle, 2))


def compute_aggregate_stats(df: "pd.DataFrame") -> dict[str, Any]:
    """
    计算时间窗口内的聚合统计字段，所有值缺失时对应字段返回 None。

    返回字典包含：
      mean_wind_speed          : float | None  —— 窗口内 10m 风速均值（m/s）
      dominant_wind_direction  : float | None  —— 圆向量法主导来风方向（°）
      dominant_wind_direction_label : str | None   —— 16 方位标签（如 "SE"）
      upwind_direction         : float | None  —— 同 dominant_wind_direction（来风）
      downwind_direction       : float | None  —— (dominant + 180) % 360（去风）
      mean_humidity            : float | None  —— 相对湿度均值（%）
      total_precipitation      : float | None  —— 累计降水量（mm）
      mean_temperature         : float | None  —— 气温均值（°C）
      mean_cloud_cover         : float | None  —— 云量均值（%）
    """
    def _mean(col: str) -> float | None:
        if col not in df.columns:
            return None
        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().all():
            return None
        return float(round(series.mean(), 4))

    def _sum(col: str) -> float | None:
        if col not in df.columns:
            return None
        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().all():
            return None
        return float(round(series.fillna(0).sum(), 4))

    dom_dir = dominant_wind_direction_from_series(
        df.get("wind_direction", pd.Series(dtype=float))
    )
    dom_label = wind_direction_label(dom_dir)
    down_dir = (
        float(round((dom_dir + 180.0) % 360.0, 2))
        if dom_dir is not None else None
    )

    return {
        "mean_wind_speed":               _mean("wind_speed"),
        "dominant_wind_direction":       dom_dir,
        "dominant_wind_direction_label": dom_label,
        "upwind_direction":              dom_dir,
        "downwind_direction":            down_dir,
        "mean_humidity":                 _mean("humidity"),
        "total_precipitation":           _sum("precipitation"),
        "mean_temperature":              _mean("temperature"),
        "mean_cloud_cover":              _mean("cloud_cover"),
    }


def compute_weather_code_summary(df: "pd.DataFrame") -> dict[str, Any]:
    """
    提取时间窗口内的天气代码摘要，处理单日与多日场景。

    单日：返回 weather_code_daily（标量 int），weather_code_daily_series 包含 1 条记录。
    多日：返回 dominant_weather_code_daily（众数，int），
          weather_code_daily_series（list[{"date": str, "weather_code": int}]，按日排序）。
    缺失：所有字段返回 None。

    聚合规则（多日）：
      dominant_weather_code_daily = 时段内各日 weather_code 的众数；
      出现频次相同时取数值最小的代码（稳定性优先）。
    weather_code_daily_series 始终包含每一日的代码，供下游按需使用。
    """
    result: dict[str, Any] = {
        "weather_code_daily":            None,
        "dominant_weather_code_daily":   None,
        "weather_code_daily_series":     None,
    }

    if "weather_code_daily" not in df.columns:
        return result

    col = pd.to_numeric(df["weather_code_daily"], errors="coerce")
    if col.isna().all():
        return result

    # 按日期分组，取每天的代表值（同一天内应相同，取众数保险）
    if "time" in df.columns:
        tmp = df[["time", "weather_code_daily"]].copy()
        tmp["_date"] = pd.to_datetime(tmp["time"]).dt.date
        daily_codes: dict[str, int] = {}
        for date, grp in tmp.groupby("_date"):
            codes = pd.to_numeric(grp["weather_code_daily"], errors="coerce").dropna()
            if codes.empty:
                continue
            mode_val = int(codes.mode().min())  # 众数（最小值优先）
            daily_codes[str(date)] = mode_val
    else:
        # 无时间列时退化：整列取众数
        codes = col.dropna()
        if codes.empty:
            return result
        daily_codes = {"unknown": int(codes.mode().min())}

    if not daily_codes:
        return result

    # 构造 series
    series = [
        {"date": d, "weather_code": c}
        for d, c in sorted(daily_codes.items())
    ]

    # 计算 dominant（所有日代码的众数）
    all_codes = list(daily_codes.values())
    from collections import Counter
    counter = Counter(all_codes)
    max_count = max(counter.values())
    dominant = min(k for k, v in counter.items() if v == max_count)

    result["dominant_weather_code_daily"] = dominant
    result["weather_code_daily_series"] = series

    if len(daily_codes) == 1:
        # 单日：额外返回标量便于简单消费
        result["weather_code_daily"] = dominant
    else:
        # 多日：weather_code_daily 设为 None，强制下游使用 series 或 dominant
        result["weather_code_daily"] = None

    return result
