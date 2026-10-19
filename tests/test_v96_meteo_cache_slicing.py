"""V9.6 回归：气象缓存的窗口切片口径。

固化一个真实缺陷：月块取数时若把结束时间写成纯日期，`floor("h")` 会落到
当天 00:00，导致**当月最后一天整天被裁掉**——表现为末日窗口的气象结果为
failed（空切片）。这里用合成帧把"含末小时"这一语义锁死，无需联网。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

V9 = Path(__file__).resolve().parent / "benchmarks" / "v9"
sys.path.insert(0, str(V9))

from meteo_cache import TIMEZONE, slice_window  # noqa: E402


def _frame(days: int = 2) -> pd.DataFrame:
    times = pd.date_range("2024-01-30 00:00", periods=24 * days, freq="h",
                          tz=TIMEZONE)
    return pd.DataFrame({"time": times, "wind_speed_10m": 1.0})


def test_slice_includes_last_hour_of_end_date() -> None:
    """末日 18:00–20:59 的窗口必须切到 3 行（历史上返回 0 行）。"""
    sliced = slice_window(_frame(), "2024-01-31 18:00:00", "2024-01-31 20:59:59")
    assert len(sliced) == 3
    assert sliced["time"].iloc[0].hour == 18
    assert sliced["time"].iloc[-1].hour == 20


def test_slice_with_date_only_end_loses_last_day() -> None:
    """把结束时间写成纯日期会丢掉当天全部小时——这就是那个缺陷的形态。"""
    sliced = slice_window(_frame(), "2024-01-31 18:00:00", "2024-01-31")
    assert len(sliced) == 0


def test_slice_is_closed_interval() -> None:
    """闭区间：起点与终点所在小时都要包含。"""
    sliced = slice_window(_frame(), "2024-01-30 05:00:00", "2024-01-30 05:00:00")
    assert len(sliced) == 1
    assert sliced["time"].iloc[0].hour == 5
