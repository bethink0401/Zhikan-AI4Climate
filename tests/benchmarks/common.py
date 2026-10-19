"""Shared helpers for the per-agent benchmark scripts."""
from __future__ import annotations

import json
import random
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: 三座路边站（与 data/station_mapping.json 一致）
ROADSIDE_STATIONS = [
    {"station_name": "Mong Kok Roadside", "station_id": "mk_roadside",
     "lat": 22.3193, "lon": 114.1694, "atc": "3006"},
    {"station_name": "Causeway Bay Roadside", "station_id": "cwb_roadside",
     "lat": 22.2801, "lon": 114.1850, "atc": "2214"},
    {"station_name": "Central Roadside", "station_id": "central_roadside",
     "lat": 22.2858, "lon": 114.1577, "atc": "2207"},
]

EVENT_WINDOW = ("2023-08-15 08:00:00", "2023-08-15 11:00:00")


def make_task(station: dict, start: str, end: str, task_id: str | None = None) -> dict:
    return {
        "task_id": task_id or f"BENCH_{station['station_id']}_{start[:16].replace(' ', '_').replace(':', '')}",
        "station_name": station["station_name"],
        "station_id": station["station_id"],
        "start_time": start,
        "end_time": end,
        "lat": station["lat"],
        "lon": station["lon"],
    }


def save_result(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


class Timer:
    def __init__(self):
        self.ms = []

    def wrap(self, fn, *args, **kwargs):
        t0 = time.perf_counter()
        out = fn(*args, **kwargs)
        self.ms.append((time.perf_counter() - t0) * 1000.0)
        return out

    @property
    def summary(self):
        return describe(self.ms)


def random_weekday_windows(n: int, year: int = 2023, hour: str = "08:00:00",
                           seed: int = 42, exclude_day_range: tuple = None) -> list[tuple[str, str]]:
    """n 个互不相同的 3 小时工作日窗口（start, end），用于对照测试。"""
    import datetime as dt
    rng = random.Random(seed)
    picked = []
    seen = set()
    while len(picked) < n:
        month = rng.randint(2, 12)
        day = rng.randint(1, 28)
        date = dt.date(year, month, day)
        if date.weekday() >= 5:  # 只要工作日
            continue
        if exclude_day_range and exclude_day_range[0] <= date.isoformat() <= exclude_day_range[1]:
            continue
        if date in seen:
            continue
        seen.add(date)
        start = f"{date.isoformat()} {hour}"
        end_dt = dt.datetime(year, month, day, int(hour[:2])) + dt.timedelta(hours=3)
        picked.append((start, end_dt.strftime("%Y-%m-%d %H:%M:%S")))
    return picked
