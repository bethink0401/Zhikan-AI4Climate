"""V9.9：把盲测年的气象与风场一次性取到本地（可断点续传）。

盲测要"一次性运行、不得中途改参数"，因此**取数这一步必须先做完**，避免运行
过程中因为网络抖动触发 `MeteorologyAgent` 的静默兜底（正是 V9.7 溯源审计
定位的那类风险）。

用法：
    /usr/bin/python3 tests/benchmarks/v9/prefetch_blind_year.py 2021
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bench_governance_v6 as gv6  # noqa: E402
import meteo_cache  # noqa: E402


def main() -> int:
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    cache_dir = HERE.parent / "results" / "meteo_cache"
    meteo_cache.install(cache_dir)
    import agents.meteorology_agent as met

    for station in gv6.STATIONS:
        print(f"=== {station['name']} / {year} ===", flush=True)
        for month in range(1, 13):
            frame, fallback, error = met.fetch_with_fallback(
                station["lat"], station["lon"],
                f"{year}-{month:02d}-01 00:00:00", f"{year}-{month:02d}-28 23:59:59",
                Path("fallback.csv"))
            status = "fallback!" if fallback else "ok"
            print(f"  {year}-{month:02d}: rows={0 if frame is None else len(frame)} "
                  f"{status} {error or ''}", flush=True)
            if fallback or frame is None or frame.empty:
                print("  ⚠️ 取数失败或降级，终止（避免污染盲测）", flush=True)
                return 1
        wind = gv6.load_annual_wind(year, station["lat"], station["lon"])
        print(f"  风场 {len(wind)} 小时", flush=True)
    print("盲测年气象与风场已全部就绪", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
