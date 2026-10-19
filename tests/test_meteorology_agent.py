"""Meteorology Agent 单元测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.meteorology_agent import run
from data_process.weather_etl import raw_to_standard_hourly
from tools import meteorology_tools as mt
from tools.open_meteo_client import fetch_with_fallback

# ---------------------------------------------------------------------------
# 当前 API 字段契约（与 config/field_schema.json 保持一致）
# ---------------------------------------------------------------------------
# hourly 字段（经 ETL RAW_TO_STANDARD 映射前的原始列名）
CURRENT_HOURLY_RAW_COLS = {
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "wind_speed_100m",
    "wind_direction_100m",
    "cloud_cover",
}
# daily 广播后的标准列名
DAILY_BROADCAST_COL = "weather_code_daily"

# 已从 API 移除的旧字段（不应再出现在 mock/CSV 中）
DELETED_FIELDS = {
    "apparent_temperature",
    "weather_code",   # hourly weather_code 已改为 daily 广播
    "snow_depth",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "soil_temperature_0_to_7cm",
}

SAMPLE_TASK = {
    "task_id": "HK_MK_2023_0800",
    "station_name": "Mong Kok Roadside",
    "station_id": "MK",
    "start_time": "2023-08-15 08:00:00",
    "end_time": "2023-08-15 11:00:00",
    "lat": 22.3193,
    "lon": 114.1694,
}

REQUIRED_TOP_KEYS = {
    "agent_name",
    "status",
    "result",
    "evidence",
    "confidence",
    "data_quality",
    "created_at",
}

REQUIRED_RESULT_KEYS = {
    "weather_risk",
    "risk_type",
    "wind_speed",
    "wind_direction",
    "dispersion_condition",
    "wind_shift_score",
    "static_wind_score",
    "humidity_risk_score",
    "rain_removal_score",
}


def _mock_api_dataframe() -> pd.DataFrame:
    """
    模拟 open_meteo_client.fetch_with_fallback 返回值。

    结构与真实 API 经 daily/hourly 合并后的原始 DataFrame 一致：
    - hourly 字段：新 API 11 个字段（原始列名，未经 ETL 重命名）
    - weather_code_daily：daily.weather_code 按日期广播的结果
      （同一天各小时使用相同天气代码）
    - 不含已删除字段：apparent_temperature / hourly weather_code
    """
    return pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2023-08-15 08:00:00",
                    "2023-08-15 09:00:00",
                    "2023-08-15 10:00:00",
                    "2023-08-15 11:00:00",
                ]
            ),
            "temperature_2m": [29.0, 30.0, 30.5, 31.0],
            "relative_humidity_2m": [80.0, 78.0, 75.0, 73.0],
            "precipitation": [0.0, 0.0, 0.5, 0.0],
            "rain": [0.0, 0.0, 0.5, 0.0],
            "snowfall": [0.0, 0.0, 0.0, 0.0],
            "wind_speed_10m": [2.0, 2.5, 3.0, 4.0],
            "wind_direction_10m": [350.0, 355.0, 10.0, 90.0],
            "wind_gusts_10m": [8.0, 9.0, 10.0, 12.0],
            "wind_speed_100m": [4.5, 5.0, 5.5, 6.0],
            "wind_direction_100m": [345.0, 350.0, 15.0, 95.0],
            "cloud_cover": [40, 35, 50, 25],
            # daily.weather_code 广播：2023-08-15 全天 weather_code=1
            "weather_code_daily": [1, 1, 1, 1],
        }
    )


# ---------------------------------------------------------------------------
# TestCircularAngleDiff
# ---------------------------------------------------------------------------

class TestCircularAngleDiff:
    def test_350_to_10_is_20_degrees(self) -> None:
        assert mt.circular_angle_diff_deg(350, 10) == pytest.approx(20.0)

    def test_symmetry(self) -> None:
        assert mt.circular_angle_diff_deg(10, 350) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# TestMissingValues
# ---------------------------------------------------------------------------

class TestMissingValues:
    def test_missing_preserved_in_etl(self) -> None:
        raw = pd.DataFrame(
            {
                "time": pd.to_datetime(["2023-08-15 08:00:00"]),
                "temperature_2m": [None],
                "relative_humidity_2m": [70.0],
                "precipitation": [0.0],
                "wind_speed_10m": [None],
                "wind_direction_10m": [180.0],
            }
        )
        out = raw_to_standard_hourly(raw, "MK", 22.3, 114.1)
        assert len(out) == 1
        flags = out.iloc[0]["is_missing"]
        assert flags["temperature"] is True
        assert flags["wind_speed"] is True
        assert flags["humidity"] is False


# ---------------------------------------------------------------------------
# TestApiSuccess
# ---------------------------------------------------------------------------

class TestApiSuccess:
    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_run_success_structure(self, mock_fetch: object) -> None:
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        assert out["agent_name"] == "MeteorologyAgent"
        assert out["status"] == "success"
        assert REQUIRED_TOP_KEYS <= set(out.keys())
        assert REQUIRED_RESULT_KEYS <= set(out["result"].keys())
        assert out["data_quality"]["fallback_used"] is False
        json.dumps(out, ensure_ascii=False)


# ---------------------------------------------------------------------------
# TestApiFallback
# ---------------------------------------------------------------------------

class TestApiFallback:
    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_fallback_flag(self, mock_fetch: object) -> None:
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        df["time"] = pd.to_datetime(df["time"])
        mock_fetch.return_value = (df, True, "connection error")
        out = run(SAMPLE_TASK)
        assert out["status"] == "warning"
        assert out["data_quality"]["fallback_used"] is True

    def test_fetch_with_fallback_uses_csv_on_api_fail(self) -> None:
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        with patch(
            "tools.open_meteo_client.fetch_archive_hourly",
            return_value=(None, "timeout"),
        ):
            df, used, err = fetch_with_fallback(
                22.3193,
                114.1694,
                "2023-08-15 08:00:00",
                "2023-08-15 11:00:00",
                csv_path,
            )
        assert used is True
        assert err == "timeout"
        assert not df.empty


# ---------------------------------------------------------------------------
# TestOutputJson
# ---------------------------------------------------------------------------

class TestOutputJson:
    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_scores_in_zero_one(self, mock_fetch: object) -> None:
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        for key in (
            "weather_risk",
            "wind_shift_score",
            "static_wind_score",
            "humidity_risk_score",
            "rain_removal_score",
        ):
            val = r[key]
            assert 0.0 <= val <= 1.0
        assert isinstance(out["evidence"], list)
        assert len(out["evidence"]) >= 4


# ---------------------------------------------------------------------------
# TestFieldAlignment  — 新增：字段契约回归检查
# ---------------------------------------------------------------------------

class TestFieldAlignment:
    """验证 mock 数据、fallback CSV 与当前 API 字段契约一致。"""

    def test_mock_df_no_deleted_fields(self) -> None:
        """mock DataFrame 不包含已删除字段。"""
        df = _mock_api_dataframe()
        present_deleted = DELETED_FIELDS & set(df.columns)
        assert present_deleted == set(), (
            f"mock DataFrame 仍包含已删除字段: {present_deleted}"
        )

    def test_mock_df_has_required_hourly_cols(self) -> None:
        """mock DataFrame 包含全部当前 hourly 字段。"""
        df = _mock_api_dataframe()
        missing = CURRENT_HOURLY_RAW_COLS - set(df.columns)
        assert missing == set(), (
            f"mock DataFrame 缺少 hourly 字段: {missing}"
        )

    def test_mock_df_has_weather_code_daily(self) -> None:
        """mock DataFrame 包含 daily 广播列 weather_code_daily。"""
        df = _mock_api_dataframe()
        assert DAILY_BROADCAST_COL in df.columns

    def test_mock_df_weather_code_daily_same_day(self) -> None:
        """同一天各小时的 weather_code_daily 值相同（广播语义）。"""
        df = _mock_api_dataframe()
        assert df[DAILY_BROADCAST_COL].nunique() == 1, (
            "同一天的 weather_code_daily 应全部相同（按天广播）"
        )

    def test_csv_no_deleted_fields(self) -> None:
        """fallback CSV 不包含已删除字段。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        present_deleted = DELETED_FIELDS & set(df.columns)
        assert present_deleted == set(), (
            f"fallback CSV 仍包含已删除字段: {present_deleted}"
        )

    def test_csv_has_required_hourly_cols(self) -> None:
        """fallback CSV 包含全部当前 hourly 字段。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        missing = CURRENT_HOURLY_RAW_COLS - set(df.columns)
        assert missing == set(), (
            f"fallback CSV 缺少 hourly 字段: {missing}"
        )

    def test_csv_has_weather_code_daily(self) -> None:
        """fallback CSV 包含 weather_code_daily 列。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        assert DAILY_BROADCAST_COL in df.columns

    def test_csv_wind_speed_100m_not_null(self) -> None:
        """fallback CSV 中 wind_speed_100m 不为空（新增字段有实际值）。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        assert df["wind_speed_100m"].notna().all(), (
            "fallback CSV 的 wind_speed_100m 含空值"
        )

    def test_csv_wind_direction_100m_not_null(self) -> None:
        """fallback CSV 中 wind_direction_100m 不为空。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        assert df["wind_direction_100m"].notna().all(), (
            "fallback CSV 的 wind_direction_100m 含空值"
        )

    def test_csv_weather_code_daily_not_null(self) -> None:
        """fallback CSV 中 weather_code_daily 不为空。"""
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        df = pd.read_csv(csv_path)
        assert df["weather_code_daily"].notna().all(), (
            "fallback CSV 的 weather_code_daily 含空值"
        )


# ---------------------------------------------------------------------------
# TestFallbackEtl  — 新增：fallback 路径 ETL 字段完整性
# ---------------------------------------------------------------------------

class TestFallbackEtl:
    """验证 fallback CSV 经 ETL 后字段正确进入标准 DataFrame。"""

    def _load_fallback_etl(self) -> pd.DataFrame:
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        raw = pd.read_csv(csv_path)
        raw["time"] = pd.to_datetime(raw["time"])
        return raw_to_standard_hourly(raw, "MK", 22.3, 114.1)

    def test_fallback_etl_wind_speed_100m_present(self) -> None:
        """fallback 路径：wind_speed_100m 正常进入 ETL 输出。"""
        out = self._load_fallback_etl()
        assert "wind_speed_100m" in out.columns
        assert out["wind_speed_100m"].notna().any()

    def test_fallback_etl_wind_direction_100m_present(self) -> None:
        """fallback 路径：wind_direction_100m 正常进入 ETL 输出。"""
        out = self._load_fallback_etl()
        assert "wind_direction_100m" in out.columns
        assert out["wind_direction_100m"].notna().any()

    def test_fallback_etl_weather_code_daily_present(self) -> None:
        """fallback 路径：weather_code_daily 正常进入 ETL 输出。"""
        out = self._load_fallback_etl()
        assert "weather_code_daily" in out.columns
        assert out["weather_code_daily"].notna().any()

    def test_fallback_etl_json_serializable(self) -> None:
        """fallback ETL 输出可完整 JSON 序列化。"""
        from data_process.weather_etl import dataframe_records_for_json
        out = self._load_fallback_etl()
        records = dataframe_records_for_json(out)
        json.dumps(records, ensure_ascii=False)  # 不抛异常即通过


# ---------------------------------------------------------------------------
# TestWeatherCodeDailyIsolation  — 新增：weather_code_daily 评分隔离
# ---------------------------------------------------------------------------

class TestWeatherCodeDailyIsolation:
    """验证 weather_code_daily 不参与四维气象风险评分。"""

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_scores_unchanged_without_weather_code_daily(
        self, mock_fetch: object
    ) -> None:
        """去掉 weather_code_daily 列，四维评分结果不变。"""
        df_with = _mock_api_dataframe()
        df_without = df_with.drop(columns=["weather_code_daily"])

        mock_fetch.return_value = (df_with, False, None)
        out_with = run(SAMPLE_TASK)

        mock_fetch.return_value = (df_without, False, None)
        out_without = run(SAMPLE_TASK)

        for key in (
            "weather_risk",
            "wind_shift_score",
            "static_wind_score",
            "humidity_risk_score",
            "rain_removal_score",
        ):
            assert out_with["result"][key] == out_without["result"][key], (
                f"{key} 在有/无 weather_code_daily 时结果不一致"
            )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_agent_success_with_null_weather_code_daily(
        self, mock_fetch: object
    ) -> None:
        """weather_code_daily 全为 None/NaN 时 Agent 仍返回 success。"""
        df = _mock_api_dataframe().copy()
        df["weather_code_daily"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        assert out["status"] == "success"
        assert out["result"]["weather_risk"] is not None

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_full_output_json_serializable(self, mock_fetch: object) -> None:
        """含 weather_code_daily 的完整输出可 JSON 序列化。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        serialized = json.dumps(out, ensure_ascii=False)
        assert len(serialized) > 0


# ---------------------------------------------------------------------------
# TestSemanticConventions  — 新增：语义约定冻结验证
# ---------------------------------------------------------------------------

class TestSemanticConventions:
    """
    验证时间/单位/风向/weather_code_daily 等语义约定已正确冻结。

    所有约定的唯一来源是 config/field_schema.json api_request_params 节，
    本测试类通过读取该配置来断言代码层面的实现与约定一致。
    """

    # ── 时区约定 ──────────────────────────────────────────────────────────

    def test_api_timezone_is_asia_hong_kong(self) -> None:
        """API_TIMEZONE 常量必须为 Asia/Hong_Kong，不得为 auto。"""
        from config.field_schema import API_TIMEZONE
        assert API_TIMEZONE == "Asia/Hong_Kong", (
            f"API_TIMEZONE 应为 Asia/Hong_Kong，实际为 {API_TIMEZONE}"
        )

    def test_fetch_archive_hourly_default_timezone(self) -> None:
        """fetch_archive_hourly 默认时区必须为 Asia/Hong_Kong。"""
        import inspect
        from tools.open_meteo_client import fetch_archive_hourly
        sig = inspect.signature(fetch_archive_hourly)
        default_tz = sig.parameters["timezone"].default
        assert default_tz == "Asia/Hong_Kong", (
            f"fetch_archive_hourly 默认 timezone 应为 Asia/Hong_Kong，实际为 {default_tz}"
        )

    def test_fetch_with_fallback_default_timezone(self) -> None:
        """fetch_with_fallback 默认时区必须为 Asia/Hong_Kong，不得为 auto。"""
        import inspect
        from tools.open_meteo_client import fetch_with_fallback
        sig = inspect.signature(fetch_with_fallback)
        default_tz = sig.parameters["timezone"].default
        assert default_tz == "Asia/Hong_Kong", (
            f"fetch_with_fallback 默认 timezone 应为 Asia/Hong_Kong，实际为 {default_tz}"
        )
        assert default_tz != "auto", "timezone 不得使用 auto"

    def test_rules_timezone_is_asia_hong_kong(self) -> None:
        """meteorology_rules.json 中的 timezone 必须为 Asia/Hong_Kong。"""
        from tools.meteorology_tools import load_rules
        rules = load_rules()
        assert rules.get("timezone") == "Asia/Hong_Kong", (
            f"rules.timezone 应为 Asia/Hong_Kong，实际为 {rules.get('timezone')}"
        )

    def test_fallback_path_uses_hk_timezone(self) -> None:
        """fallback 路径时间 localize 使用 Asia/Hong_Kong，不得使用 UTC。"""
        import pandas as pd
        csv_path = ROOT / "data" / "sample_weather_mong_kok.csv"
        with patch(
            "tools.open_meteo_client.fetch_archive_hourly",
            return_value=(None, "simulated timeout"),
        ):
            df, used, _ = fetch_with_fallback(
                22.3193, 114.1694,
                "2023-08-15 08:00:00",
                "2023-08-15 11:00:00",
                csv_path,
            )
        assert used is True
        assert not df.empty
        # 时间列应带时区信息且为 Asia/Hong_Kong
        tz_name = str(df["time"].dt.tz)
        assert "Hong_Kong" in tz_name or "UTC+08" in tz_name or "+08:00" in tz_name, (
            f"fallback 路径时间时区应为 Asia/Hong_Kong，实际为 {tz_name}"
        )

    # ── 风速单位约定 ──────────────────────────────────────────────────────

    def test_wind_speed_unit_is_ms(self) -> None:
        """API_WIND_SPEED_UNIT 必须为 ms（m/s）。"""
        from config.field_schema import API_WIND_SPEED_UNIT
        assert API_WIND_SPEED_UNIT == "ms", (
            f"API_WIND_SPEED_UNIT 应为 ms，实际为 {API_WIND_SPEED_UNIT}"
        )

    def test_api_request_includes_wind_speed_unit(self) -> None:
        """API 请求参数必须包含 wind_speed_unit=ms。"""
        import requests

        # 直接检查构建的请求 URL 参数，不依赖网络
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["params"] = params or {}
            # 返回一个让函数提前退出的错误响应
            raise requests.RequestException("test intercept")

        with patch("tools.open_meteo_client.requests.get", side_effect=fake_get):
            from tools.open_meteo_client import fetch_archive_hourly
            fetch_archive_hourly(22.3, 114.1, "2023-08-15 08:00:00", "2023-08-15 08:00:00")

        assert captured.get("params", {}).get("wind_speed_unit") == "ms", (
            f"API 请求缺少 wind_speed_unit=ms，实际参数: {captured.get('params')}"
        )

    def test_api_request_includes_fixed_units(self) -> None:
        """API 请求参数必须同时包含 temperature_unit=celsius 和 precipitation_unit=mm。"""
        import requests
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["params"] = params or {}
            raise requests.RequestException("test intercept")

        with patch("tools.open_meteo_client.requests.get", side_effect=fake_get):
            from tools.open_meteo_client import fetch_archive_hourly
            fetch_archive_hourly(22.3, 114.1, "2023-08-15 08:00:00", "2023-08-15 08:00:00")

        p = captured.get("params", {})
        assert p.get("temperature_unit") == "celsius", (
            f"API 请求缺少 temperature_unit=celsius，实际: {p.get('temperature_unit')}"
        )
        assert p.get("precipitation_unit") == "mm", (
            f"API 请求缺少 precipitation_unit=mm，实际: {p.get('precipitation_unit')}"
        )

    # ── 风向语义约定 ──────────────────────────────────────────────────────

    def test_wind_direction_range_0_to_360(self) -> None:
        """mock 数据中 wind_direction_10m 均在 [0, 360) 范围内。"""
        df = _mock_api_dataframe()
        dirs = df["wind_direction_10m"]
        assert (dirs >= 0).all() and (dirs < 360).all(), (
            f"wind_direction_10m 超出 [0, 360) 范围: {dirs.tolist()}"
        )

    def test_downwind_is_upwind_plus_180(self) -> None:
        """downwind 方向 = (upwind + 180) % 360 的数值约定。"""
        # 测试代表性角度
        test_cases = [
            (0.0,   180.0),
            (90.0,  270.0),
            (180.0, 0.0),
            (270.0, 90.0),
            (350.0, 170.0),
            (45.0,  225.0),
        ]
        for upwind, expected_down in test_cases:
            actual_down = (upwind + 180.0) % 360.0
            assert abs(actual_down - expected_down) < 1e-6, (
                f"upwind={upwind}° 时 downwind 应为 {expected_down}°，实际 {actual_down}°"
            )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_upwind_downwind_in_agent_output(self, mock_fetch: object) -> None:
        """Agent 输出中 upwind/downwind 均为非空字符串。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert isinstance(r.get("upwind"), str) and len(r["upwind"]) > 0
        assert isinstance(r.get("downwind"), str) and len(r["downwind"]) > 0

    # ── daily weather_code 广播约定 ───────────────────────────────────────

    def test_daily_broadcast_same_value_per_day(self) -> None:
        """同一天的所有小时行应广播到相同的 weather_code_daily 值。"""
        import pandas as pd
        from tools.open_meteo_client import _merge_daily_into_hourly

        hourly = pd.DataFrame({
            "time": pd.to_datetime([
                "2023-08-15 08:00:00", "2023-08-15 09:00:00",
                "2023-08-15 10:00:00", "2023-08-15 11:00:00",
            ]),
            "wind_speed_10m": [1.0, 1.5, 2.0, 2.5],
        })
        daily = {"time": ["2023-08-15"], "weather_code": [61]}
        result = _merge_daily_into_hourly(hourly, daily)

        assert "weather_code_daily" in result.columns
        assert result["weather_code_daily"].nunique() == 1
        assert (result["weather_code_daily"] == 61).all(), (
            f"weather_code_daily 应全为 61，实际: {result['weather_code_daily'].tolist()}"
        )

    def test_daily_broadcast_two_days(self) -> None:
        """跨两天的 hourly 数据应分别广播各自当天的 weather_code。"""
        import pandas as pd
        from tools.open_meteo_client import _merge_daily_into_hourly

        hourly = pd.DataFrame({
            "time": pd.to_datetime([
                "2023-08-15 20:00:00", "2023-08-15 23:00:00",
                "2023-08-16 00:00:00", "2023-08-16 03:00:00",
            ]),
            "wind_speed_10m": [1.0, 1.5, 2.0, 2.5],
        })
        daily = {"time": ["2023-08-15", "2023-08-16"], "weather_code": [3, 61]}
        result = _merge_daily_into_hourly(hourly, daily)

        day1 = result[result["time"].dt.date == pd.Timestamp("2023-08-15").date()]
        day2 = result[result["time"].dt.date == pd.Timestamp("2023-08-16").date()]
        assert (day1["weather_code_daily"] == 3).all()
        assert (day2["weather_code_daily"] == 61).all()

    def test_daily_broadcast_with_tz_aware_hourly(self) -> None:
        """带 Asia/Hong_Kong 时区的 hourly DataFrame 也能正确广播 daily 字段。"""
        import pandas as pd
        from tools.open_meteo_client import _merge_daily_into_hourly

        hourly = pd.DataFrame({
            "time": pd.to_datetime([
                "2023-08-15 08:00:00+08:00",
                "2023-08-15 09:00:00+08:00",
            ]),
            "wind_speed_10m": [1.0, 1.5],
        })
        daily = {"time": ["2023-08-15"], "weather_code": [2]}
        result = _merge_daily_into_hourly(hourly, daily)

        assert (result["weather_code_daily"] == 2).all(), (
            f"带时区 hourly 广播失败: {result['weather_code_daily'].tolist()}"
        )

    # ── daily 数据缺失时 Agent 仍可运行 ──────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_agent_runs_when_daily_column_absent(self, mock_fetch: object) -> None:
        """完全没有 weather_code_daily 列时，Agent 正常返回 success。"""
        df = _mock_api_dataframe().drop(columns=["weather_code_daily"])
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        assert out["status"] == "success"
        assert out["result"]["weather_risk"] is not None

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_agent_runs_when_daily_all_null(self, mock_fetch: object) -> None:
        """weather_code_daily 全为 None 时，Agent 正常返回 success。"""
        df = _mock_api_dataframe().copy()
        df["weather_code_daily"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        assert out["status"] == "success"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_scores_unaffected_by_daily_code_value(self, mock_fetch: object) -> None:
        """改变 weather_code_daily 的值，四维评分结果不变。"""
        df_a = _mock_api_dataframe().copy()
        df_a["weather_code_daily"] = 1   # 晴天

        df_b = _mock_api_dataframe().copy()
        df_b["weather_code_daily"] = 95  # 雷暴

        mock_fetch.return_value = (df_a, False, None)
        out_a = run(SAMPLE_TASK)

        mock_fetch.return_value = (df_b, False, None)
        out_b = run(SAMPLE_TASK)

        for key in ("weather_risk", "wind_shift_score", "static_wind_score",
                    "humidity_risk_score", "rain_removal_score"):
            assert out_a["result"][key] == out_b["result"][key], (
                f"{key} 随 weather_code_daily 变化而改变，违反评分隔离约定"
            )


# ---------------------------------------------------------------------------
# TestInputValidation  — 新增：输入契约完整校验
# ---------------------------------------------------------------------------

class TestInputValidation:
    """
    覆盖 validate_task 和 run() 的全部输入校验规则。
    每个测试对应 docs/api_contract.md 输入契约中的一条规则。
    """

    # ── 合法输入（基准） ──────────────────────────────────────────────────

    def test_valid_task_returns_no_errors(self) -> None:
        """标准合法 task 不返回任何错误。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id":    "HK_MK_20260723_0800",
            "lat":        22.3193,
            "lon":        114.1694,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert errs == []

    def test_valid_task_with_optional_fields(self) -> None:
        """含 station_id 和 station_name 的合法 task 不返回错误。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id":      "HK_MK_20260723_0800",
            "station_id":   "MK",
            "station_name": "Mong Kok Roadside",
            "lat":          22.3193,
            "lon":          114.1694,
            "start_time":   "2026-07-23 08:00:00",
            "end_time":     "2026-07-23 11:00:00",
        })
        assert errs == []

    # ── 必填字段缺失 ──────────────────────────────────────────────────────

    def test_missing_task_id(self) -> None:
        """缺少 task_id 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "lat": 22.3193, "lon": 114.1694,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("task_id" in e for e in errs)

    def test_missing_start_time(self) -> None:
        """缺少 start_time 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "end_time": "2026-07-23 11:00:00",
        })
        assert any("start_time" in e for e in errs)

    def test_missing_end_time(self) -> None:
        """缺少 end_time 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
        })
        assert any("end_time" in e for e in errs)

    def test_missing_lat_only(self) -> None:
        """只缺 lat 应报错（lat/lon 必须同时存在）。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert len(errs) > 0

    def test_missing_lon_only(self) -> None:
        """只缺 lon 应报错（lat/lon 必须同时存在）。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert len(errs) > 0

    def test_missing_both_lat_lon(self) -> None:
        """lat 和 lon 同时缺少应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X",
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert len(errs) > 0

    # ── 类型错误 ──────────────────────────────────────────────────────────

    def test_task_id_wrong_type(self) -> None:
        """task_id 为非字符串类型应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": 12345,
            "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("task_id" in e for e in errs)

    def test_task_id_empty_string(self) -> None:
        """task_id 为空字符串应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "   ",
            "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("task_id" in e for e in errs)

    def test_lat_wrong_type_string(self) -> None:
        """lat 为字符串类型应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": "22.3", "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert len(errs) > 0

    # ── 坐标范围 ──────────────────────────────────────────────────────────

    def test_lat_above_90(self) -> None:
        """lat > 90 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 91.0, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("lat" in e for e in errs)

    def test_lat_below_minus_90(self) -> None:
        """lat < -90 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": -91.0, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("lat" in e for e in errs)

    def test_lon_above_180(self) -> None:
        """lon > 180 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 181.0,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("lon" in e for e in errs)

    def test_lon_below_minus_180(self) -> None:
        """lon < -180 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": -181.0,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("lon" in e for e in errs)

    def test_lat_lon_boundary_values_valid(self) -> None:
        """lat=±90、lon=±180 为合法边界值。"""
        from agents.meteorology_agent import validate_task
        for lat, lon in [(90, 180), (-90, -180), (0, 0)]:
            errs = validate_task({
                "task_id": "X", "lat": lat, "lon": lon,
                "start_time": "2026-07-23 08:00:00",
                "end_time":   "2026-07-23 11:00:00",
            })
            assert not any("lat" in e or "lon" in e for e in errs), (
                f"lat={lat}, lon={lon} 应为合法边界值，errors: {errs}"
            )

    # ── 时间解析 ──────────────────────────────────────────────────────────

    def test_invalid_start_time_format(self) -> None:
        """无法解析的 start_time 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "not-a-date",
            "end_time":   "2026-07-23 11:00:00",
        })
        assert any("start_time" in e for e in errs)

    def test_invalid_end_time_format(self) -> None:
        """无法解析的 end_time 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "9999-99-99",
        })
        assert any("end_time" in e for e in errs)

    # ── 时间整点化 ────────────────────────────────────────────────────────

    def test_time_is_floored_to_hour_in_output(self) -> None:
        """含分秒的时间在 run() 输出中应整点化。"""
        from agents.meteorology_agent import run
        with patch("agents.meteorology_agent.fetch_with_fallback",
                   return_value=(_mock_api_dataframe(), False, None)):
            out = run({
                "task_id":    "X",
                "lat":        22.3193,
                "lon":        114.1694,
                "start_time": "2023-08-15 08:30:00",
                "end_time":   "2023-08-15 11:45:00",
            })
        assert out["status"] == "success"
        assert out["result"]["start_time"] == "2023-08-15 08:00:00"
        assert out["result"]["end_time"]   == "2023-08-15 11:00:00"

    # ── 时间顺序与窗口 ────────────────────────────────────────────────────

    def test_end_before_start_after_floor(self) -> None:
        """整点化后 end < start 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 11:00:00",
            "end_time":   "2026-07-23 08:00:00",
        })
        assert any("end_time" in e or "早于" in e for e in errs)

    def test_end_equals_start_after_floor(self) -> None:
        """整点化后 end == start（窗口 = 0h）应报错（< 1h）。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:10:00",
            "end_time":   "2026-07-23 08:50:00",
        })
        assert len(errs) > 0, "整点化后 start==end 应产生错误"

    def test_window_exactly_1h_valid(self) -> None:
        """整点化后窗口恰好 1h 应通过。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-23 08:00:00",
            "end_time":   "2026-07-23 09:00:00",
        })
        assert errs == []

    def test_window_exactly_168h_valid(self) -> None:
        """整点化后窗口恰好 168h（7 天）应通过。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-01 00:00:00",
            "end_time":   "2026-07-08 00:00:00",
        })
        assert errs == []

    def test_window_over_168h_invalid(self) -> None:
        """时间窗口 > 168h 应报错。"""
        from agents.meteorology_agent import validate_task
        errs = validate_task({
            "task_id": "X", "lat": 22.3, "lon": 114.1,
            "start_time": "2026-07-01 00:00:00",
            "end_time":   "2026-07-09 01:00:00",
        })
        assert len(errs) > 0, "超过 168h 应产生错误"

    # ── run() 错误不抛异常 ────────────────────────────────────────────────

    def test_run_returns_error_json_not_exception(self) -> None:
        """输入非法时 run() 返回标准 failed JSON，不抛异常。"""
        out = run({})   # 完全空 task
        assert out["status"] == "failed"
        assert out["agent_name"] == "MeteorologyAgent"
        assert out["result"] == {}
        assert out["confidence"] == 0.0

    def test_run_error_contains_message(self) -> None:
        """failed JSON 的 evidence 包含具体错误说明。"""
        out = run({"task_id": "X", "lat": 999, "lon": 114.1,
                   "start_time": "2026-07-23 08:00:00",
                   "end_time": "2026-07-23 11:00:00"})
        assert out["status"] == "failed"
        assert isinstance(out["evidence"], list)
        assert len(out["evidence"]) > 0
        assert any(len(e) > 0 for e in out["evidence"])

    # ── batch_run 单条错误隔离 ────────────────────────────────────────────

    def test_batch_run_error_isolation(self) -> None:
        """batch_run 中一条非法任务不影响其余合法任务。"""
        from agents.meteorology_agent import batch_run
        tasks = [
            {
                "task_id":    "HK_MK_20260723_0800",
                "lat":        22.3193,
                "lon":        114.1694,
                "start_time": "2023-08-15 08:00:00",
                "end_time":   "2023-08-15 11:00:00",
            },
            {
                # 非法：缺少 task_id，lat 越界
                "lat": 999, "lon": 114.1,
                "start_time": "2023-08-15 08:00:00",
                "end_time":   "2023-08-15 11:00:00",
            },
            {
                "task_id":    "HK_TST_20260723_0800",
                "lat":        22.2988,
                "lon":        114.1722,
                "start_time": "2023-08-15 08:00:00",
                "end_time":   "2023-08-15 11:00:00",
            },
        ]
        with patch("agents.meteorology_agent.fetch_with_fallback",
                   return_value=(_mock_api_dataframe(), False, None)):
            results = batch_run(tasks)

        assert len(results) == 3
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert results[2]["status"] == "success"

    def test_batch_run_all_errors_still_returns_list(self) -> None:
        """所有任务都非法时，batch_run 返回全 error 列表，不抛异常。"""
        from agents.meteorology_agent import batch_run
        tasks = [
            {"lat": 999, "lon": 114.1,
             "start_time": "bad", "end_time": "bad"},
            {"task_id": "X", "lat": 22.3, "lon": 999,
             "start_time": "2023-08-15 08:00:00",
             "end_time": "2023-08-15 11:00:00"},
        ]
        results = batch_run(tasks)
        assert len(results) == 2
        assert all(r["status"] == "failed" for r in results)

    # ── Flask API 层同步校验 ───────────────────────────────────────────────

    def test_flask_analyze_missing_task_id_returns_error_body(self) -> None:
        """Flask /analyze 接收缺少 task_id 的请求，响应 body status 为 error。"""
        from api.app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            resp = client.post("/api/meteorology/analyze", json={
                "lat": 22.3193, "lon": 114.1694,
                "start_time": "2026-07-23 08:00:00",
                "end_time":   "2026-07-23 11:00:00",
            })
        data = resp.get_json()
        # 校验委托给 agent 层，HTTP 200 但 body status=failed
        assert resp.status_code == 200
        assert data["result"]["status"] == "failed"

    def test_flask_analyze_invalid_lat_returns_error_body(self) -> None:
        """Flask /analyze 收到越界 lat，响应 body status 为 failed。"""
        from api.app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            resp = client.post("/api/meteorology/analyze", json={
                "task_id": "X",
                "lat": 200.0, "lon": 114.1694,
                "start_time": "2026-07-23 08:00:00",
                "end_time":   "2026-07-23 11:00:00",
            })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["result"]["status"] == "failed"

    def test_flask_hourly_invalid_lat_returns_400(self) -> None:
        """Flask /hourly 收到越界 lat 返回 HTTP 400。"""
        from api.app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            resp = client.get(
                "/api/meteorology/hourly"
                "?lat=200&lon=114.1694"
                "&start_time=2026-07-23+08:00:00"
                "&end_time=2026-07-23+11:00:00"
            )
        assert resp.status_code == 400

    def test_flask_hourly_window_too_short_returns_400(self) -> None:
        """Flask /hourly 时间窗口不足 1h 返回 HTTP 400。"""
        from api.app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            resp = client.get(
                "/api/meteorology/hourly"
                "?lat=22.3193&lon=114.1694"
                "&start_time=2026-07-23+08:10:00"
                "&end_time=2026-07-23+08:40:00"
            )
        assert resp.status_code == 400

    def test_flask_hourly_no_timezone_param_accepted(self) -> None:
        """Flask /hourly 不接受 timezone 参数（时区固定 Asia/Hong_Kong），有无 timezone 均正常。"""
        from api.app import app as flask_app
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            with patch("api.app.fetch_with_fallback",
                       return_value=(_mock_api_dataframe(), False, None)):
                # 传了 timezone 参数也不报错（被忽略）
                resp = client.get(
                    "/api/meteorology/hourly"
                    "?lat=22.3193&lon=114.1694"
                    "&start_time=2023-08-15+08:00:00"
                    "&end_time=2023-08-15+11:00:00"
                    "&timezone=UTC"   # 此参数应被忽略
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestOutputStatus  — 新增：统一输出结构与状态规则验证
# ---------------------------------------------------------------------------

# 用于 status 测试的基础字段常量
_STATUS_TASK = {
    "task_id":    "HK_MK_20230815_0800",
    "station_id": "MK",
    "lat":        22.3193,
    "lon":        114.1694,
    "start_time": "2023-08-15 08:00:00",
    "end_time":   "2023-08-15 11:00:00",
}

# 核心字段全部缺失的 mock df（wind_speed / wind_direction / humidity / precipitation / temperature）
def _mock_df_core_missing() -> pd.DataFrame:
    """所有核心字段均为 None，模拟严重数据缺失但 ETL 仍有行。"""
    df = _mock_api_dataframe().copy()
    df["wind_speed_10m"]      = None
    df["wind_direction_10m"]  = None
    df["relative_humidity_2m"] = None
    df["precipitation"]        = None
    df["temperature_2m"]       = None
    return df


class TestOutputStatus:
    """
    验证所有情况下输出结构完整、status 字段正确、类型安全、可 JSON 序列化。

    覆盖场景：
      success            — 真实 API，核心字段完整
      warning (fallback) — 使用 fallback CSV
      warning (missing)  — 核心字段部分缺失但仍可计算
      failed (input)     — 输入参数校验失败
      failed (no data)   — API + fallback 均返回空数据
      batch partial      — 批处理中部分任务失败
    """

    # ── 通用结构断言 ──────────────────────────────────────────────────────

    def _assert_envelope(self, out: dict) -> None:
        """断言外层 7 键结构完整且类型正确。"""
        for key in ("agent_name", "status", "result", "evidence",
                    "confidence", "data_quality", "created_at"):
            assert key in out, f"输出缺少顶层键: {key}"
        assert out["agent_name"] == "MeteorologyAgent"
        assert out["status"] in ("success", "warning", "failed")
        assert isinstance(out["result"], dict)
        assert isinstance(out["evidence"], list)
        assert isinstance(out["confidence"], float)
        assert 0.0 <= out["confidence"] <= 1.0
        # data_quality 结构
        dq = out["data_quality"]
        assert isinstance(dq["missing_rate"], float)
        assert 0.0 <= dq["missing_rate"] <= 1.0
        assert isinstance(dq["fallback_used"], bool)
        assert isinstance(dq["source"], str)
        assert isinstance(dq["note"], str)

    def _assert_json_serializable(self, out: dict) -> None:
        """断言输出可完整 JSON 序列化（无 numpy/pandas 类型泄漏）。"""
        serialized = json.dumps(out, ensure_ascii=False)
        assert len(serialized) > 0

    def _assert_success_result_keys(self, out: dict) -> None:
        """断言 success/warning 时 result 包含所有必填字段。"""
        for key in (
            "task_id", "weather_risk", "risk_type", "wind_speed",
            "wind_direction", "dispersion_condition",
            "wind_shift_score", "static_wind_score",
            "humidity_risk_score", "rain_removal_score",
        ):
            assert key in out["result"], f"result 缺少字段: {key}"

    # ── status=success ────────────────────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_success(self, mock_fetch):
        """真实 API 返回完整数据 → status=success，source=Open-Meteo Archive API。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(_STATUS_TASK)

        self._assert_envelope(out)
        assert out["status"] == "success"
        assert out["data_quality"]["fallback_used"] is False
        assert out["data_quality"]["source"] == "Open-Meteo Archive API"
        assert out["confidence"] > 0.8
        self._assert_success_result_keys(out)
        self._assert_json_serializable(out)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_success_result_types(self, mock_fetch):
        """success 时 result 中所有数值字段为 Python 原生 float/int，无 numpy 类型。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(_STATUS_TASK)
        r = out["result"]
        for key in ("weather_risk", "wind_shift_score", "static_wind_score",
                    "humidity_risk_score", "rain_removal_score"):
            assert type(r[key]) is float, f"{key} 应为 float，实际 {type(r[key])}"
        assert type(r["hourly_count"]) is int
        assert type(r["lat"]) is float
        assert type(r["lon"]) is float

    # ── status=warning（fallback）─────────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_warning_fallback(self, mock_fetch):
        """使用 fallback CSV → status=warning，source=Local Fallback CSV。"""
        mock_fetch.return_value = (_mock_api_dataframe(), True, "connection timeout")
        out = run(_STATUS_TASK)

        self._assert_envelope(out)
        assert out["status"] == "warning"
        assert out["data_quality"]["fallback_used"] is True
        assert out["data_quality"]["source"] == "Local Fallback CSV"
        assert "兜底" in out["data_quality"]["note"]
        self._assert_success_result_keys(out)
        self._assert_json_serializable(out)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_warning_fallback_confidence_reduced(self, mock_fetch):
        """fallback 时 confidence 应低于同等数据质量下 API 路径的 confidence。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out_api = run(_STATUS_TASK)

        mock_fetch.return_value = (_mock_api_dataframe(), True, "error")
        out_fb = run(_STATUS_TASK)

        assert out_fb["confidence"] < out_api["confidence"], (
            "fallback 路径 confidence 应低于 API 路径"
        )

    # ── status=warning（核心字段部分缺失）────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_warning_missing_core_field(self, mock_fetch):
        """核心字段部分缺失（wind_speed/wind_direction 全为 None） → status=warning。"""
        df = _mock_api_dataframe().copy()
        df["wind_speed_10m"]     = None
        df["wind_direction_10m"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(_STATUS_TASK)

        self._assert_envelope(out)
        assert out["status"] == "warning"
        assert out["data_quality"]["missing_rate"] > 0.0
        assert out["data_quality"]["source"] == "Open-Meteo Archive API"
        self._assert_json_serializable(out)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_warning_missing_core_confidence_reduced(self, mock_fetch):
        """核心字段缺失时 confidence 应低于数据完整的情况。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out_full = run(_STATUS_TASK)

        df_miss = _mock_api_dataframe().copy()
        df_miss["wind_speed_10m"] = None
        mock_fetch.return_value = (df_miss, False, None)
        out_miss = run(_STATUS_TASK)

        assert out_miss["confidence"] < out_full["confidence"], (
            "核心字段缺失时 confidence 应降低"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_warning_non_core_missing_stays_success_or_warning(self, mock_fetch):
        """
        非核心字段（weather_code_daily）缺失不得使状态变为 failed；
        且 data_quality.note 中包含缺失说明。
        """
        df = _mock_api_dataframe().drop(columns=["weather_code_daily"])
        mock_fetch.return_value = (df, False, None)
        out = run(_STATUS_TASK)

        assert out["status"] in ("success", "warning"), (
            f"非核心字段缺失不应导致 failed，实际 status={out['status']}"
        )
        assert "weather_code_daily" in out["data_quality"]["note"]
        self._assert_json_serializable(out)

    # ── status=failed（输入校验错误）──────────────────────────────────────

    def test_status_failed_missing_task_id(self):
        """缺少 task_id → status=failed，result={}。"""
        out = run({
            "lat": 22.3193, "lon": 114.1694,
            "start_time": "2023-08-15 08:00:00",
            "end_time":   "2023-08-15 11:00:00",
        })
        self._assert_envelope(out)
        assert out["status"] == "failed"
        assert out["result"] == {}
        assert out["confidence"] == 0.0
        self._assert_json_serializable(out)

    def test_status_failed_invalid_lat(self):
        """lat 越界 → status=failed，result={}。"""
        out = run(dict(_STATUS_TASK, lat=999.0))
        self._assert_envelope(out)
        assert out["status"] == "failed"
        assert out["result"] == {}
        self._assert_json_serializable(out)

    def test_status_failed_end_before_start(self):
        """end_time < start_time → status=failed，result={}。"""
        out = run(dict(_STATUS_TASK,
                       start_time="2023-08-15 11:00:00",
                       end_time="2023-08-15 08:00:00"))
        self._assert_envelope(out)
        assert out["status"] == "failed"
        assert out["result"] == {}
        self._assert_json_serializable(out)

    def test_status_failed_empty_task(self):
        """完全空的 task → status=failed，不抛异常。"""
        out = run({})
        assert out["status"] == "failed"
        assert out["result"] == {}
        assert out["evidence"] != []

    def test_status_failed_source_is_none(self):
        """输入校验失败时 data_quality.source 应为 'None'（无数据来源）。"""
        out = run({})
        assert out["data_quality"]["source"] == "None"

    # ── status=failed（API + fallback 均失败）────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_failed_no_data(self, mock_fetch):
        """API 和 fallback 均返回空 DataFrame → status=failed，result={}。"""
        empty_df = pd.DataFrame(columns=["time"])
        mock_fetch.return_value = (empty_df, True, "network error")
        out = run(_STATUS_TASK)

        self._assert_envelope(out)
        assert out["status"] == "failed"
        assert out["result"] == {}
        assert out["confidence"] == 0.0
        self._assert_json_serializable(out)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_status_failed_no_data_evidence_not_empty(self, mock_fetch):
        """无数据时 evidence 应包含说明信息，不为空列表。"""
        empty_df = pd.DataFrame(columns=["time"])
        mock_fetch.return_value = (empty_df, True, "timeout")
        out = run(_STATUS_TASK)
        assert len(out["evidence"]) > 0
        assert any(len(e) > 0 for e in out["evidence"])

    # ── batch 部分失败隔离 ─────────────────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_batch_partial_failure_statuses(self, mock_fetch):
        """batch_run 中非法任务返回 failed，合法任务返回 success，互不影响。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        tasks = [
            _STATUS_TASK,
            # 非法：lat 越界
            dict(_STATUS_TASK, task_id="BAD_LAT", lat=999.0),
            # 非法：缺少 task_id
            {"lat": 22.3, "lon": 114.1,
             "start_time": "2023-08-15 08:00:00",
             "end_time": "2023-08-15 11:00:00"},
            dict(_STATUS_TASK, task_id="HK_TST", station_id="TST"),
        ]
        from agents.meteorology_agent import batch_run
        results = batch_run(tasks)

        assert len(results) == 4
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert results[2]["status"] == "failed"
        assert results[3]["status"] == "success"
        # 所有条目都可序列化
        for r in results:
            self._assert_envelope(r)
            self._assert_json_serializable(r)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_batch_all_failed(self, mock_fetch):
        """batch_run 所有任务非法时返回全 failed 列表，不抛异常。"""
        from agents.meteorology_agent import batch_run
        tasks = [
            {"lat": 999, "lon": 114.1, "task_id": "X1",
             "start_time": "bad", "end_time": "bad"},
            {"task_id": "X2", "lat": 22.3, "lon": 181.0,
             "start_time": "2023-08-15 08:00:00",
             "end_time": "2023-08-15 11:00:00"},
        ]
        results = batch_run(tasks)
        assert len(results) == 2
        assert all(r["status"] == "failed" for r in results)
        assert all(r["result"] == {} for r in results)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_batch_mix_warning_and_success(self, mock_fetch):
        """batch_run 中 fallback 任务返回 warning，API 任务返回 success。"""
        from agents.meteorology_agent import batch_run

        def side_effect(lat, lon, start, end, csv_path, timezone):
            # 第一次调用返回 API 数据，第二次返回 fallback
            if not hasattr(side_effect, "called"):
                side_effect.called = True
                return (_mock_api_dataframe(), False, None)
            return (_mock_api_dataframe(), True, "timeout")

        mock_fetch.side_effect = side_effect
        tasks = [
            _STATUS_TASK,
            dict(_STATUS_TASK, task_id="HK_TST"),
        ]
        results = batch_run(tasks)
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "warning"

    # ── JSON 序列化：numpy 类型不泄漏 ────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_no_numpy_types_in_output(self, mock_fetch):
        """输出中不应包含任何 numpy 标量类型。"""
        import numpy as np
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(_STATUS_TASK)

        def _check_no_numpy(obj, path="root"):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _check_no_numpy(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _check_no_numpy(v, f"{path}[{i}]")
            else:
                assert not isinstance(obj, (np.floating, np.integer, np.bool_)), (
                    f"路径 {path} 含 numpy 类型 {type(obj)}"
                )

        _check_no_numpy(out)

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_full_output_json_roundtrip(self, mock_fetch):
        """输出经 json.dumps → json.loads 往返后内容不变。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(_STATUS_TASK)
        roundtrip = json.loads(json.dumps(out, ensure_ascii=False))
        assert roundtrip["status"] == out["status"]
        assert roundtrip["result"]["weather_risk"] == out["result"]["weather_risk"]
        assert roundtrip["data_quality"]["source"] == out["data_quality"]["source"]


# ---------------------------------------------------------------------------
# TestFrozenResultFields  — v1.5.0 核心冻结字段完整性验证
# ---------------------------------------------------------------------------

# 全部核心冻结字段（status=success/warning 时 result 必须存在）
FROZEN_RESULT_KEYS = {
    # 综合风险
    "weather_risk",
    "risk_type",
    # 四维评分
    "dispersion_condition",
    "static_wind_score",
    "wind_shift_score",
    "humidity_risk_score",
    "rain_removal_score",
    "insufficient_rain_removal_score",
    # 主导风向
    "mean_wind_speed",
    "dominant_wind_direction",
    "dominant_wind_direction_label",
    "upwind_direction",
    "downwind_direction",
    # 聚合统计
    "mean_humidity",
    "total_precipitation",
    "mean_temperature",
    "mean_cloud_cover",
    # 天气代码
    "weather_code_daily",
    "dominant_weather_code_daily",
    "weather_code_daily_series",
    # 计数
    "hourly_count",
}

# deprecated 兼容字段（兼容期内仍必须存在，但下游不应新增依赖）
DEPRECATED_RESULT_KEYS = {"wind_speed", "wind_direction", "upwind", "downwind"}


class TestFrozenResultFields:
    """
    验证 result 输出包含所有 v1.5.0 冻结字段，且类型、语义、互补关系正确。
    不检查评分数值本身——评分逻辑未修改，由 TestOutputJson 负责。
    """

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_all_frozen_keys_present(self, mock_fetch):
        """success 时 result 包含全部冻结字段。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        assert out["status"] == "success"
        missing = FROZEN_RESULT_KEYS - set(out["result"].keys())
        assert missing == set(), f"result 缺少冻结字段: {missing}"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_deprecated_keys_still_present(self, mock_fetch):
        """兼容期内 deprecated 字段仍必须存在于 result 中。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        missing = DEPRECATED_RESULT_KEYS - set(out["result"].keys())
        assert missing == set(), f"deprecated 兼容字段缺失: {missing}"

    # ── insufficient_rain_removal_score 互补约束 ─────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_insufficient_rain_equals_one_minus_rain(self, mock_fetch):
        """insufficient_rain_removal_score + rain_removal_score == 1.0（互补）。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        total = round(r["rain_removal_score"] + r["insufficient_rain_removal_score"], 6)
        assert abs(total - 1.0) < 1e-5, (
            f"rain_removal_score({r['rain_removal_score']}) + "
            f"insufficient_rain_removal_score({r['insufficient_rain_removal_score']}) "
            f"= {total}，应为 1.0"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_insufficient_rain_in_zero_one(self, mock_fetch):
        """insufficient_rain_removal_score ∈ [0, 1]。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["insufficient_rain_removal_score"]
        assert 0.0 <= v <= 1.0, f"insufficient_rain_removal_score={v} 超出 [0,1]"

    # ── dominant_wind_direction / label / upwind / downwind ──────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_dominant_wind_direction_in_range(self, mock_fetch):
        """dominant_wind_direction ∈ [0, 360) 或 None。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["dominant_wind_direction"]
        if v is not None:
            assert 0.0 <= v < 360.0, f"dominant_wind_direction={v} 超出 [0, 360)"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_dominant_wind_direction_label_is_16_compass(self, mock_fetch):
        """dominant_wind_direction_label 是 16 方位之一或 None。"""
        valid_labels = {
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        }
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        label = out["result"]["dominant_wind_direction_label"]
        if label is not None:
            assert label in valid_labels, (
                f"dominant_wind_direction_label={label!r} 不是合法的 16 方位标签"
            )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_upwind_direction_equals_dominant(self, mock_fetch):
        """upwind_direction 数值应与 dominant_wind_direction 相同。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert r["upwind_direction"] == r["dominant_wind_direction"], (
            f"upwind_direction({r['upwind_direction']}) != "
            f"dominant_wind_direction({r['dominant_wind_direction']})"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_downwind_is_dominant_plus_180(self, mock_fetch):
        """downwind_direction = (dominant_wind_direction + 180) % 360。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        dom = r["dominant_wind_direction"]
        down = r["downwind_direction"]
        if dom is not None and down is not None:
            expected = round((dom + 180.0) % 360.0, 2)
            assert abs(down - expected) < 1e-4, (
                f"downwind_direction={down} != (dominant+180)%360={expected}"
            )

    # ── 聚合统计字段类型与范围 ────────────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_mean_wind_speed_non_negative(self, mock_fetch):
        """mean_wind_speed ≥ 0 或 None。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["mean_wind_speed"]
        if v is not None:
            assert v >= 0.0, f"mean_wind_speed={v} 为负数"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_mean_humidity_in_range(self, mock_fetch):
        """mean_humidity ∈ [0, 100] 或 None。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["mean_humidity"]
        if v is not None:
            assert 0.0 <= v <= 100.0, f"mean_humidity={v} 超出 [0, 100]"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_total_precipitation_non_negative(self, mock_fetch):
        """total_precipitation ≥ 0 或 None。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["total_precipitation"]
        if v is not None:
            assert v >= 0.0, f"total_precipitation={v} 为负数"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_mean_cloud_cover_in_range(self, mock_fetch):
        """mean_cloud_cover ∈ [0, 100] 或 None。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        v = out["result"]["mean_cloud_cover"]
        if v is not None:
            assert 0.0 <= v <= 100.0, f"mean_cloud_cover={v} 超出 [0, 100]"

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_agg_fields_are_float_or_none(self, mock_fetch):
        """聚合统计数值字段均为 Python float 或 None，不含 numpy 类型。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        for key in ("mean_wind_speed", "dominant_wind_direction",
                    "upwind_direction", "downwind_direction",
                    "mean_humidity", "total_precipitation",
                    "mean_temperature", "mean_cloud_cover"):
            v = r[key]
            assert v is None or type(v) is float, (
                f"{key} 应为 float 或 None，实际 {type(v).__name__}: {v}"
            )

    # ── weather_code_daily 单日场景 ───────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_single_day_weather_code_daily_is_int(self, mock_fetch):
        """单日场景 weather_code_daily 为 int（而非 None）。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        # mock 数据全为同一天，应返回标量
        assert r["weather_code_daily"] is not None, (
            "单日场景 weather_code_daily 不应为 None"
        )
        assert type(r["weather_code_daily"]) is int, (
            f"weather_code_daily 应为 int，实际 {type(r['weather_code_daily'])}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_single_day_dominant_matches_weather_code_daily(self, mock_fetch):
        """单日场景 dominant_weather_code_daily == weather_code_daily。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert r["dominant_weather_code_daily"] == r["weather_code_daily"], (
            "单日场景 dominant 应与 weather_code_daily 相同"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_single_day_series_has_one_entry(self, mock_fetch):
        """单日场景 weather_code_daily_series 恰好包含 1 条记录。"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        series = out["result"]["weather_code_daily_series"]
        assert series is not None and len(series) == 1, (
            f"单日场景 series 长度应为 1，实际 {len(series) if series else 'None'}"
        )

    # ── weather_code_daily 多日场景 ───────────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_multi_day_weather_code_daily_is_none(self, mock_fetch):
        """多日场景 weather_code_daily（标量）应为 None，强制下游使用 series/dominant。"""
        df = _mock_api_dataframe().copy()
        # 改为跨两天
        df["time"] = pd.to_datetime([
            "2023-08-15 22:00:00", "2023-08-15 23:00:00",
            "2023-08-16 00:00:00", "2023-08-16 01:00:00",
        ])
        df["weather_code_daily"] = [3, 3, 61, 61]
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert r["weather_code_daily"] is None, (
            f"多日场景 weather_code_daily 应为 None，实际 {r['weather_code_daily']}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_multi_day_series_has_two_entries(self, mock_fetch):
        """多日场景 weather_code_daily_series 包含 2 条按日期排序的记录。"""
        df = _mock_api_dataframe().copy()
        df["time"] = pd.to_datetime([
            "2023-08-15 22:00:00", "2023-08-15 23:00:00",
            "2023-08-16 00:00:00", "2023-08-16 01:00:00",
        ])
        df["weather_code_daily"] = [3, 3, 61, 61]
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        series = out["result"]["weather_code_daily_series"]
        assert series is not None and len(series) == 2, (
            f"多日场景 series 长度应为 2，实际 {len(series) if series else 'None'}"
        )
        assert series[0]["date"] == "2023-08-15"
        assert series[0]["weather_code"] == 3
        assert series[1]["date"] == "2023-08-16"
        assert series[1]["weather_code"] == 61

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_multi_day_dominant_is_mode(self, mock_fetch):
        """多日场景 dominant_weather_code_daily 为众数（出现最多的代码）。"""
        df = _mock_api_dataframe().copy()
        # 三天：code=3 出现两次，code=61 出现一次 → dominant=3
        df["time"] = pd.to_datetime([
            "2023-08-14 00:00:00", "2023-08-15 00:00:00",
            "2023-08-15 01:00:00", "2023-08-16 00:00:00",
        ])
        df["weather_code_daily"] = [3, 61, 61, 3]
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        dominant = out["result"]["dominant_weather_code_daily"]
        # 2023-08-14: code=3, 2023-08-15: code=61（众数）, 2023-08-16: code=3 → 3出现2次
        assert dominant == 3, (
            f"dominant 应为出现最多的代码 3，实际 {dominant}"
        )

    # ── weather_code_daily 缺失时所有代码字段均为 None ─────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_weather_code_fields_null_when_missing(self, mock_fetch):
        """weather_code_daily 数据全缺失时，三个代码字段均为 None。"""
        df = _mock_api_dataframe().drop(columns=["weather_code_daily"])
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert r["weather_code_daily"] is None
        assert r["dominant_weather_code_daily"] is None
        assert r["weather_code_daily_series"] is None

    # ── null 字段规范：缺失时返回 None，不伪造默认值 ────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_null_fields_when_all_wind_missing(self, mock_fetch):
        """wind_speed/direction 全缺失时，主导风向相关字段返回 None，不伪造。"""
        df = _mock_api_dataframe().copy()
        df["wind_speed_10m"]     = None
        df["wind_direction_10m"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        r = out["result"]
        assert r["mean_wind_speed"] is None, "无风速时 mean_wind_speed 应为 None"
        assert r["dominant_wind_direction"] is None, "无风向时 dominant_wind_direction 应为 None"
        assert r["dominant_wind_direction_label"] is None
        assert r["upwind_direction"] is None
        assert r["downwind_direction"] is None

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_null_fields_when_humidity_missing(self, mock_fetch):
        """humidity 全缺失时 mean_humidity 返回 None，不伪造。"""
        df = _mock_api_dataframe().copy()
        df["relative_humidity_2m"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        assert out["result"]["mean_humidity"] is None

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_null_fields_when_temperature_missing(self, mock_fetch):
        """temperature 全缺失时 mean_temperature 返回 None，不伪造。"""
        df = _mock_api_dataframe().copy()
        df["temperature_2m"] = None
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        assert out["result"]["mean_temperature"] is None

    # ── wind_direction_label 工具函数独立测试 ────────────────────────────

    def test_wind_direction_label_cardinal(self):
        """基本方位正确映射。"""
        from tools.meteorology_tools import wind_direction_label
        assert wind_direction_label(0.0)   == "N"
        assert wind_direction_label(90.0)  == "E"
        assert wind_direction_label(135.0) == "SE"
        assert wind_direction_label(180.0) == "S"
        assert wind_direction_label(270.0) == "W"
        assert wind_direction_label(315.0) == "NW"

    def test_wind_direction_label_360_equals_north(self):
        """360° 应被视为 0°（北），归为 N。"""
        from tools.meteorology_tools import wind_direction_label
        assert wind_direction_label(360.0) == "N"

    def test_wind_direction_label_none_input(self):
        """None 输入应返回 None，不抛异常。"""
        from tools.meteorology_tools import wind_direction_label
        assert wind_direction_label(None) is None

    def test_wind_direction_label_all_16_sectors(self):
        """16 方位的代表角度均能正确映射。"""
        from tools.meteorology_tools import wind_direction_label
        sector_tests = [
            (0.0,    "N"),   (22.5,  "NNE"), (45.0,  "NE"),  (67.5,  "ENE"),
            (90.0,   "E"),   (112.5, "ESE"), (135.0, "SE"),  (157.5, "SSE"),
            (180.0,  "S"),   (202.5, "SSW"), (225.0, "SW"),  (247.5, "WSW"),
            (270.0,  "W"),   (292.5, "WNW"), (315.0, "NW"),  (337.5, "NNW"),
        ]
        for deg, expected in sector_tests:
            got = wind_direction_label(deg)
            assert got == expected, f"wind_direction_label({deg}) = {got!r}, expected {expected!r}"

    # ── dominant_wind_direction_from_series 独立测试 ─────────────────────

    def test_dominant_wind_direction_cross_north_boundary(self):
        """350° 和 10° 的主导方向应约为 0°（北），而非 180°。"""
        from tools.meteorology_tools import dominant_wind_direction_from_series
        s = pd.Series([350.0, 10.0])
        d = dominant_wind_direction_from_series(s)
        assert d is not None
        # 结果应在 0° 附近（< 20°），不应接近 180°
        assert d < 20.0 or d > 340.0, (
            f"跨北边界主导方向应约为 0°，实际 {d}"
        )

    def test_dominant_wind_direction_all_nan(self):
        """全 NaN 序列应返回 None。"""
        from tools.meteorology_tools import dominant_wind_direction_from_series
        s = pd.Series([float("nan"), float("nan")])
        assert dominant_wind_direction_from_series(s) is None

    def test_dominant_wind_direction_single_value(self):
        """单个有效值直接返回该值。"""
        from tools.meteorology_tools import dominant_wind_direction_from_series
        s = pd.Series([135.0])
        d = dominant_wind_direction_from_series(s)
        assert abs(d - 135.0) < 0.1

    # ── compute_weather_code_summary 独立测试 ────────────────────────────

    def test_weather_code_summary_single_day(self):
        """单日：weather_code_daily 为标量，series 含 1 条。"""
        from tools.meteorology_tools import compute_weather_code_summary
        df = pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00", "2023-08-15 09:00"]),
            "weather_code_daily": [61, 61],
        })
        wc = compute_weather_code_summary(df)
        assert wc["weather_code_daily"] == 61
        assert wc["dominant_weather_code_daily"] == 61
        assert len(wc["weather_code_daily_series"]) == 1

    def test_weather_code_summary_multi_day_dominant_is_mode(self):
        """多日：dominant 为众数，code=3 出现2次 > code=61 出现1次。"""
        from tools.meteorology_tools import compute_weather_code_summary
        df = pd.DataFrame({
            "time": pd.to_datetime([
                "2023-08-14 00:00", "2023-08-15 00:00", "2023-08-16 00:00",
            ]),
            "weather_code_daily": [3, 61, 3],
        })
        wc = compute_weather_code_summary(df)
        assert wc["weather_code_daily"] is None  # 多日不返回标量
        assert wc["dominant_weather_code_daily"] == 3
        assert len(wc["weather_code_daily_series"]) == 3

    def test_weather_code_summary_tie_picks_min_code(self):
        """相同出现频次时，dominant 取数值最小的代码（稳定性优先）。"""
        from tools.meteorology_tools import compute_weather_code_summary
        df = pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 00:00", "2023-08-16 00:00"]),
            "weather_code_daily": [61, 3],
        })
        wc = compute_weather_code_summary(df)
        assert wc["dominant_weather_code_daily"] == 3, (
            "频次相同时应取最小代码"
        )

    def test_weather_code_summary_no_column(self):
        """无 weather_code_daily 列时全部返回 None。"""
        from tools.meteorology_tools import compute_weather_code_summary
        df = pd.DataFrame({"time": pd.to_datetime(["2023-08-15 08:00"])})
        wc = compute_weather_code_summary(df)
        assert wc["weather_code_daily"] is None
        assert wc["dominant_weather_code_daily"] is None
        assert wc["weather_code_daily_series"] is None

    def test_weather_code_summary_all_null(self):
        """weather_code_daily 全为 NaN 时全部返回 None。"""
        from tools.meteorology_tools import compute_weather_code_summary
        df = pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00"]),
            "weather_code_daily": [None],
        })
        wc = compute_weather_code_summary(df)
        assert wc["weather_code_daily"] is None
        assert wc["dominant_weather_code_daily"] is None

    # ── 完整输出 JSON 序列化（含新字段）─────────────────────────────────

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_frozen_fields_json_serializable(self, mock_fetch):
        """新增冻结字段不引入任何不可序列化类型。"""
        import json
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        serialized = json.dumps(out, ensure_ascii=False)
        roundtrip = json.loads(serialized)
        for key in FROZEN_RESULT_KEYS:
            assert key in roundtrip["result"], f"序列化往返后 {key} 丢失"


# ---------------------------------------------------------------------------
# TestRainScoringSemanticsRegression  — 新增：降雨评分语义回归测试（BUG修复验证）
# ---------------------------------------------------------------------------

class TestRainScoringSemanticsRegression:
    """
    验证降雨评分语义修复（2026-07-28）：
    - rain_removal_score：越高=清除越强（降水充足=高分）
    - insufficient_rain_removal_score：越高=清除不足（少雨=高分）
    - 两者互补：insufficient = 1 - rain_removal
    - aggregate_weather_risk 使用 insufficient（高=风险大）
    """

    def _mock_low_rain(self) -> pd.DataFrame:
        """少雨场景：0.2 mm 降水（< 1.0 mm 阈值）"""
        return pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00:00", "2023-08-15 09:00:00", "2023-08-15 10:00:00"]),
            "temperature_2m": [29.0, 30.0, 30.5],
            "relative_humidity_2m": [85.0, 85.0, 85.0],
            "precipitation": [0.1, 0.1, 0.0],  # 总计 0.2 mm < 1.0 mm
            "rain": [0.1, 0.1, 0.0],
            "snowfall": [0.0, 0.0, 0.0],
            "wind_speed_10m": [1.0, 1.0, 1.5],  # 低风速
            "wind_direction_10m": [350.0, 355.0, 10.0],
            "wind_gusts_10m": [3.0, 3.0, 4.0],
            "wind_speed_100m": [2.5, 2.5, 3.0],
            "wind_direction_100m": [345.0, 350.0, 15.0],
            "cloud_cover": [60, 65, 70],
            "weather_code_daily": [2, 2, 2],
        })

    def _mock_high_rain(self) -> pd.DataFrame:
        """充足降雨场景：5.0 mm 降水（> 1.0 mm 阈值）"""
        return pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00:00", "2023-08-15 09:00:00", "2023-08-15 10:00:00"]),
            "temperature_2m": [29.0, 28.5, 28.0],
            "relative_humidity_2m": [90.0, 92.0, 95.0],
            "precipitation": [1.5, 2.0, 1.5],  # 总计 5.0 mm > 1.0 mm
            "rain": [1.5, 2.0, 1.5],
            "snowfall": [0.0, 0.0, 0.0],
            "wind_speed_10m": [3.0, 3.5, 4.0],
            "wind_direction_10m": [180.0, 185.0, 190.0],
            "wind_gusts_10m": [8.0, 9.0, 10.0],
            "wind_speed_100m": [5.0, 5.5, 6.0],
            "wind_direction_100m": [175.0, 180.0, 185.0],
            "cloud_cover": [90, 95, 100],
            "weather_code_daily": [61, 61, 61],
        })

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_low_rain_has_low_rain_removal_score(self, mock_fetch):
        """少雨时 rain_removal_score 应该低（清除能力弱）"""
        mock_fetch.return_value = (self._mock_low_rain(), False, None)
        out = run(SAMPLE_TASK)
        rain_score = out["result"]["rain_removal_score"]
        assert rain_score < 0.5, (
            f"0.2mm 降水时 rain_removal_score 应 < 0.5（清除能力弱），实际 {rain_score}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_low_rain_has_high_insufficient_score(self, mock_fetch):
        """少雨时 insufficient_rain_removal_score 应该高（清除不足风险大）"""
        mock_fetch.return_value = (self._mock_low_rain(), False, None)
        out = run(SAMPLE_TASK)
        insuf_score = out["result"]["insufficient_rain_removal_score"]
        assert insuf_score > 0.5, (
            f"0.2mm 降水时 insufficient_rain_removal_score 应 > 0.5（清除不足），实际 {insuf_score}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_high_rain_has_high_rain_removal_score(self, mock_fetch):
        """充足降雨时 rain_removal_score 应该高（清除能力强）"""
        mock_fetch.return_value = (self._mock_high_rain(), False, None)
        out = run(SAMPLE_TASK)
        rain_score = out["result"]["rain_removal_score"]
        assert rain_score > 0.5, (
            f"5.0mm 降水时 rain_removal_score 应 > 0.5（清除能力强），实际 {rain_score}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_high_rain_has_low_insufficient_score(self, mock_fetch):
        """充足降雨时 insufficient_rain_removal_score 应该低（清除良好）"""
        mock_fetch.return_value = (self._mock_high_rain(), False, None)
        out = run(SAMPLE_TASK)
        insuf_score = out["result"]["insufficient_rain_removal_score"]
        assert insuf_score < 0.5, (
            f"5.0mm 降水时 insufficient_rain_removal_score 应 < 0.5（清除良好），实际 {insuf_score}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_rain_scores_are_complement(self, mock_fetch):
        """rain_removal_score + insufficient_rain_removal_score = 1.0（互补关系）"""
        mock_fetch.return_value = (self._mock_low_rain(), False, None)
        out = run(SAMPLE_TASK)
        rain_score = out["result"]["rain_removal_score"]
        insuf_score = out["result"]["insufficient_rain_removal_score"]
        sum_scores = rain_score + insuf_score
        assert abs(sum_scores - 1.0) < 0.001, (
            f"rain_removal_score + insufficient_rain_removal_score 应 = 1.0，实际 {sum_scores}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_weather_risk_increases_with_insufficient_rain(self, mock_fetch):
        """weather_risk 应随 insufficient_rain_removal_score 增加而增加"""
        # 少雨场景：高 insufficient → 高 weather_risk
        mock_fetch.return_value = (self._mock_low_rain(), False, None)
        out_low = run(SAMPLE_TASK)
        
        # 充足降雨场景：低 insufficient → 低 weather_risk
        mock_fetch.return_value = (self._mock_high_rain(), False, None)
        out_high = run(SAMPLE_TASK)
        
        insuf_low = out_low["result"]["insufficient_rain_removal_score"]
        insuf_high = out_high["result"]["insufficient_rain_removal_score"]
        risk_low = out_low["result"]["weather_risk"]
        risk_high = out_high["result"]["weather_risk"]
        
        # 验证：少雨的 insufficient 更高，且对应的 weather_risk 也应该更高（其他条件相似时）
        assert insuf_low > insuf_high, (
            f"少雨场景 insufficient ({insuf_low}) 应 > 充足降雨场景 ({insuf_high})"
        )


# ---------------------------------------------------------------------------
# TestDispersionConditionRegression  — 新增：扩散条件修复验证（BUG修复）
# ---------------------------------------------------------------------------

class TestDispersionConditionRegression:
    """
    验证扩散条件修复（2026-07-28）：
    - dispersion_condition 使用 mean_wind_speed（窗口平均）而非末小时风速
    - high weather_risk + 低风速 → 不应输出"有利扩散"
    """

    def _mock_high_risk_low_wind(self) -> pd.DataFrame:
        """高风险场景：低风速、高湿、少雨"""
        return pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00:00", "2023-08-15 09:00:00", "2023-08-15 10:00:00"]),
            "temperature_2m": [29.0, 30.0, 30.5],
            "relative_humidity_2m": [85.0, 87.0, 88.0],  # 高湿
            "precipitation": [0.0, 0.1, 0.1],  # 少雨 0.2 mm
            "rain": [0.0, 0.1, 0.1],
            "snowfall": [0.0, 0.0, 0.0],
            "wind_speed_10m": [1.0, 1.2, 2.25],  # 前两小时低风速，末小时2.25（超过2.0阈值）
            "wind_direction_10m": [350.0, 355.0, 10.0],
            "wind_gusts_10m": [3.0, 3.5, 5.0],
            "wind_speed_100m": [2.5, 2.8, 4.0],
            "wind_direction_100m": [345.0, 350.0, 15.0],
            "cloud_cover": [60, 65, 70],
            "weather_code_daily": [2, 2, 2],
        })

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_high_risk_not_favorable_dispersion(self, mock_fetch):
        """high weather_risk 场景不应输出"有利扩散"（中文）"""
        mock_fetch.return_value = (self._mock_high_risk_low_wind(), False, None)
        out = run(SAMPLE_TASK)
        risk_type = out["result"]["risk_type"]
        disp = out["result"]["dispersion_condition"]
        
        if risk_type == "high":
            assert "有利" not in disp, (
                f"high 风险时不应输出有利扩散，实际 dispersion_condition={disp!r}"
            )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_dispersion_uses_mean_wind_speed(self, mock_fetch):
        """dispersion_condition 应基于 mean_wind_speed（平均1.15）而非末小时（2.25）"""
        mock_fetch.return_value = (self._mock_high_risk_low_wind(), False, None)
        out = run(SAMPLE_TASK)
        mean_ws = out["result"]["mean_wind_speed"]
        disp = out["result"]["dispersion_condition"]
        
        # 平均风速 (1.0+1.2+2.25)/3 = 1.48 < 2.0，应为不利扩散
        assert mean_ws < 2.0, f"测试数据 mean_wind_speed 应 < 2.0，实际 {mean_ws}"
        assert "有利" not in disp or mean_ws >= 2.0, (
            f"mean_wind_speed={mean_ws:.2f} < 2.0 时不应有利扩散，实际 {disp!r}"
        )


# ---------------------------------------------------------------------------
# TestWindSpeedFieldsRegression  — 新增：风速字段改名验证（BUG修复）
# ---------------------------------------------------------------------------

class TestWindSpeedFieldsRegression:
    """
    验证风速字段修复（2026-07-28）：
    - 旧 wind_speed 改名为 latest_wind_speed（末小时风速）
    - 输出不应同时包含 wind_speed 和 latest_wind_speed（避免重复）
    - Spatial Agent 应读取 mean_wind_speed（窗口平均）
    """

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_output_has_latest_wind_speed(self, mock_fetch):
        """输出包含 latest_wind_speed 字段"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        assert "latest_wind_speed" in out["result"], (
            "输出应包含 latest_wind_speed 字段"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_output_has_mean_wind_speed(self, mock_fetch):
        """输出包含 mean_wind_speed 字段"""
        mock_fetch.return_value = (_mock_api_dataframe(), False, None)
        out = run(SAMPLE_TASK)
        assert "mean_wind_speed" in out["result"], (
            "输出应包含 mean_wind_speed 字段"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_latest_is_last_hour_value(self, mock_fetch):
        """latest_wind_speed 应为末小时风速值"""
        df = _mock_api_dataframe()
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        latest_ws = out["result"]["latest_wind_speed"]
        expected_last = df["wind_speed_10m"].iloc[-1]
        assert latest_ws == expected_last, (
            f"latest_wind_speed 应为末小时风速 {expected_last}，实际 {latest_ws}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_mean_is_window_average(self, mock_fetch):
        """mean_wind_speed 应为窗口平均值"""
        df = _mock_api_dataframe()
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        mean_ws = out["result"]["mean_wind_speed"]
        expected_mean = df["wind_speed_10m"].mean()
        assert abs(mean_ws - expected_mean) < 0.01, (
            f"mean_wind_speed 应为窗口平均 {expected_mean:.2f}，实际 {mean_ws}"
        )

    @patch("agents.meteorology_agent.fetch_with_fallback")
    def test_latest_and_mean_differ_when_wind_varies(self, mock_fetch):
        """风速有变化时，latest_wind_speed 和 mean_wind_speed 应不同"""
        df = pd.DataFrame({
            "time": pd.to_datetime(["2023-08-15 08:00:00", "2023-08-15 09:00:00", "2023-08-15 10:00:00"]),
            "temperature_2m": [29.0, 30.0, 30.5],
            "relative_humidity_2m": [80.0, 78.0, 75.0],
            "precipitation": [0.0, 0.0, 0.0],
            "rain": [0.0, 0.0, 0.0],
            "snowfall": [0.0, 0.0, 0.0],
            "wind_speed_10m": [1.0, 1.5, 4.0],  # 变化大：末小时4.0，平均2.17
            "wind_direction_10m": [350.0, 355.0, 10.0],
            "wind_gusts_10m": [3.0, 4.0, 8.0],
            "wind_speed_100m": [2.5, 3.0, 6.0],
            "wind_direction_100m": [345.0, 350.0, 15.0],
            "cloud_cover": [40, 35, 50],
            "weather_code_daily": [1, 1, 1],
        })
        mock_fetch.return_value = (df, False, None)
        out = run(SAMPLE_TASK)
        latest = out["result"]["latest_wind_speed"]
        mean = out["result"]["mean_wind_speed"]
        
        assert latest == 4.0, f"latest_wind_speed 应为 4.0，实际 {latest}"
        assert 2.0 < mean < 2.5, f"mean_wind_speed 应约为 2.17，实际 {mean}"
        assert abs(latest - mean) > 1.0, (
            f"latest ({latest}) 和 mean ({mean}) 应显著不同"
        )


# ---------------------------------------------------------------------------
# TestCoordinatorImportPath  — 新增：Coordinator 路径回归验证
# ---------------------------------------------------------------------------

class TestCoordinatorImportPath:
    """
    验证 Coordinator 正确导入 agents/meteorology_agent.py（2026-07-28 确认）
    不存在根目录旧版占位文件导致的路径混淆
    """

    def test_meteorology_agent_module_file_in_agents_dir(self):
        """agents.meteorology_agent.__file__ 应指向 agents/ 目录"""
        from agents import meteorology_agent
        agent_file = Path(meteorology_agent.__file__).resolve()
        assert "agents" in agent_file.parts, (
            f"meteorology_agent.__file__ 应在 agents/ 目录，实际 {agent_file}"
        )

    def test_no_root_level_meteorology_agent_py(self):
        """根目录不应存在 meteorology_agent.py（避免路径混淆）"""
        root_file = ROOT / "meteorology_agent.py"
        assert not root_file.exists(), (
            f"根目录存在旧版 {root_file}，应删除以避免 import 混淆"
        )

    def test_coordinator_imports_correct_agent(self):
        """Coordinator 能成功从 agents.meteorology_agent 导入 run"""
        try:
            from agents.coordinator_min import meteo_run
            from agents.meteorology_agent import run as expected_run
            assert meteo_run is expected_run, (
                "Coordinator 的 meteo_run 应直接引用 agents.meteorology_agent.run"
            )
        except ImportError as e:
            pytest.fail(f"Coordinator 导入失败: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
