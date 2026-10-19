"""V9.9：把盲测年的气象与风场一次性取到本地（**并发**、可断点续传）。

与 `prefetch_blind_year.py` 的关系：那个是串行的参考实现（每月一次请求），本脚本
只把"按月取数"这一步并发化——**取数口径完全相同**（同一个 `fetch_archive_hourly`、
同样的 `[月初 00:00:00, 月末 23:59:59]` 区间、同样的落盘路径与文件名），因此产出的
缓存与串行版逐字节同源。

盲测要"一次性运行、不得中途改参数"，所以取数必须先全部完成，避免运行期间网络抖动
触发 `MeteorologyAgent` 的静默兜底（V9.7 溯源审计定位的那类风险）。

用法：
    /usr/bin/python3 tests/benchmarks/v9/prefetch_blind_year_parallel.py 2021 [并发数]
"""

from __future__ import annotations

import calendar
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bench_governance_v6 as gv6  # noqa: E402
from tools.open_meteo_client import fetch_archive_hourly  # noqa: E402

TIMEZONE = "Asia/Hong_Kong"
CACHE_DIR = HERE.parent / "results" / "meteo_cache"


def _month(lat: float, lon: float, year: int, month: int) -> str:
    path = (CACHE_DIR / f"meteo_{year}_{float(lat):.4f}_{float(lon):.4f}_{month:02d}.csv")
    if path.exists():
        return f"{year}-{month:02d} 已有（跳过）"
    last_day = calendar.monthrange(year, month)[1]
    error = None
    for attempt in range(5):
        frame, error = fetch_archive_hourly(
            lat, lon, f"{year}-{month:02d}-01 00:00:00",
            f"{year}-{month:02d}-{last_day:02d} 23:59:59",
            timezone=TIMEZONE, timeout_sec=60.0)
        if frame is not None and not frame.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
            return f"{year}-{month:02d} ok rows={len(frame)}"
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{year}-{month:02d} @ {lat},{lon} 取数失败：{error}")


def main() -> int:
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    jobs = [(s["lat"], s["lon"], month)
            for s in gv6.STATIONS for month in range(1, 13)]
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_month, lat, lon, year, month): (lat, lon, month)
                   for lat, lon, month in jobs}
        for future in as_completed(futures):
            lat, lon, month = futures[future]
            try:
                print(f"  {future.result()}  ({lat},{lon})", flush=True)
            except Exception as exc:  # noqa: BLE001 - 汇总后统一报错
                failures.append(f"{year}-{month:02d} ({lat},{lon}): {exc}")
                print(f"  ✗ {failures[-1]}", flush=True)
    if failures:
        print(f"⚠️ {len(failures)} 个月取数失败（可重跑，已成功的不再重取）", flush=True)
        return 1

    for station in gv6.STATIONS:
        wind = gv6.load_annual_wind(year, station["lat"], station["lon"])
        print(f"  风场 {station['name']}: {len(wind)} 小时", flush=True)
    print("盲测年气象与风场已全部就绪", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
