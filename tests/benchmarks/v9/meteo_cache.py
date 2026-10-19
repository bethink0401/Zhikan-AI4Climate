"""V9.6：气象取数的年度磁盘缓存（基准可复现性设施）。

背景：`MeteorologyAgent` 每个窗口都要向 Open-Meteo 取一次数（本机实测 15–18 s），
254 个留出窗口因此要跑一个多小时，而且**每次跑都要重新联网**——既不快，也不
可复现（网络抖动会改变结果）。

做法：只在**取数层**注入缓存——把 `agents.meteorology_agent.fetch_with_fallback`
替换为"按站点整年取一次、再从内存切片"的版本，Agent 自己的 ETL 与四维评分
代码一行不改（因此不存在公式复刻造成的偏差）。

接口对齐：原函数返回 `(df, fallback_used, api_error)`。联网路径虽然按日期请求，
但会用 `[start.floor('h'), end.floor('h')]` 闭区间过滤到小时，因此切片口径必须
完全一致；daily 字段（weather_code_daily）在整年帧里已按日广播，切片天然保留。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_TIMEZONE = "Asia/Hong_Kong"
_BASE = Path(__file__).resolve().parents[3]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
TIMEZONE = _TIMEZONE


def slice_window(frame: "pd.DataFrame", start_time: str, end_time: str) -> "pd.DataFrame":
    """按联网路径同口径切片：`[start.floor(h), end.floor(h)]` 闭区间。

    单独抽成纯函数以便单测——V9.6 曾因传纯日期（end 落到当天 00:00）导致
    **每个月最后一天的 24 小时被整体裁掉**，表现为末日窗口气象返回 failed。
    """
    start = pd.Timestamp(start_time)
    end = pd.Timestamp(end_time)
    if start.tzinfo is None:
        start = start.tz_localize(TIMEZONE)
    if end.tzinfo is None:
        end = end.tz_localize(TIMEZONE)
    low, high = start.floor("h"), end.floor("h")
    sliced = frame[(frame["time"] >= low) & (frame["time"] <= high)]
    return sliced.reset_index(drop=True)


def install(cache_dir: Path) -> None:
    """把年度缓存装载进 MeteorologyAgent 的取数入口。"""
    import agents.meteorology_agent as met
    from tools.open_meteo_client import fetch_archive_hourly

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[tuple[float, float, int], pd.DataFrame] = {}

    def _month_frame(lat: float, lon: float, year: int, month: int) -> pd.DataFrame:
        """按月取数并缓存——整年一次请求在弱网下几乎必然超时，按月分块可重试、
        可断点续传（已成功的月份不再重取）。"""
        import calendar
        import time

        path = (cache_dir /
                f"meteo_{year}_{round(float(lat), 4):.4f}_{round(float(lon), 4):.4f}"
                f"_{month:02d}.csv")
        if path.exists():
            return pd.read_csv(path, parse_dates=["time"])
        last_day = calendar.monthrange(year, month)[1]
        error = None
        for attempt in range(5):
            # 结束时间必须带到 23:59:59：fetch_archive_hourly 会按 floor("h")
            # 裁剪，传纯日期会把当月最后一天整天裁掉（V9.6 修复的缺陷）。
            frame, error = fetch_archive_hourly(
                lat, lon, f"{year}-{month:02d}-01 00:00:00",
                f"{year}-{month:02d}-{last_day:02d} 23:59:59",
                timezone=TIMEZONE, timeout_sec=60.0)
            if frame is not None and not frame.empty:
                frame.to_csv(path, index=False)
                return frame
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(
            f"气象取数失败（{year}-{month:02d} @ {lat},{lon}，已重试 5 次）：{error}")

    def _year_frame(lat: float, lon: float, year: int) -> pd.DataFrame:
        key = (round(float(lat), 4), round(float(lon), 4), int(year))
        if key in frames:
            return frames[key]
        parts = [_month_frame(lat, lon, year, month) for month in range(1, 13)]
        frame = pd.concat(parts).drop_duplicates("time").sort_values("time")
        frame = frame.reset_index(drop=True)
        if frame["time"].dt.tz is None:
            frame["time"] = frame["time"].dt.tz_localize(TIMEZONE)
        frames[key] = frame
        return frame

    def cached_fetch(lat, lon, start_time, end_time, fallback_csv, timezone=TIMEZONE):
        start = pd.Timestamp(start_time)
        end = pd.Timestamp(end_time)
        frame = _year_frame(lat, lon, int(start.year))
        if end.year != start.year:
            frame = pd.concat([frame, _year_frame(lat, lon, int(end.year))])
            frame = (frame.drop_duplicates("time").sort_values("time")
                     .reset_index(drop=True))
            frames[(round(float(lat), 4), round(float(lon), 4), int(start.year))] = frame
        return slice_window(frame, start_time, end_time), False, None

    met.fetch_with_fallback = cached_fetch


def main() -> int:
    """自检：缓存切片与**联网原始帧**逐值一致（重试避免网络抖动干扰）。

    注意不能拿 Agent 的评分做比对：Agent 内部有结果缓存，且联网失败时会
    静默降级到兜底 CSV，会把"网络问题"误判成"缓存不一致"。
    """
    import time

    from tools.open_meteo_client import fetch_archive_hourly

    install(_BASE / "tests" / "benchmarks" / "results" / "meteo_cache")
    import agents.meteorology_agent as met

    probes = [
        (22.3227, 114.1683, "2024-07-06 07:00:00", "2024-07-06 09:59:59"),
        (22.2801, 114.1850, "2024-02-15 18:00:00", "2024-02-15 20:59:59"),
        (22.3227, 114.1683, "2024-11-28 07:00:00", "2024-11-28 09:59:59"),
    ]
    failures = 0
    for lat, lon, start, end in probes:
        live = None
        for attempt in range(3):
            live, error = fetch_archive_hourly(lat, lon, start, end)
            if live is not None and not live.empty:
                break
            print(f"  联网失败（{attempt + 1}/3）：{error}", flush=True)
            time.sleep(5)
        if live is None or live.empty:
            print(f"  跳过 {start}：联网三次均失败", flush=True)
            continue
        cached, _, _ = met.fetch_with_fallback(lat, lon, start, end, Path("x"))
        left = live.reset_index(drop=True).copy()
        right = cached.reset_index(drop=True).copy()
        # CSV 往返会把时区名写成 UTC+08:00，统一换算到 UTC 再逐列比对
        left["time"] = left["time"].dt.tz_convert("UTC")
        right["time"] = right["time"].dt.tz_convert("UTC")
        same = (list(left.columns) == list(right.columns) and len(left) == len(right)
                and all(left[c].astype(str).tolist() == right[c].astype(str).tolist()
                        for c in left.columns))
        print(f"  {start} 行数 {len(live)} vs {len(cached)} 一致={same}", flush=True)
        if not same:
            failures += 1
    print("缓存与联网原始帧一致 ✔" if failures == 0 else f"⚠️ 有 {failures} 个窗口不一致")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
