# -*- coding: utf-8 -*-
"""
AQHI（空气质量健康指数）下载与合并。

EPICDI 的逐小时数据里只有污染物**浓度**（SO2/NOX/NO2/CO/RSP/O3/FSP），没有 AQHI。
AQHI 是环保署另外按月发布的 CSV：

    https://www.aqhi.gov.hk/epd/ddata/html/history/{年}/{年月}_Eng.csv

本脚本把它拉下来，按（站点, 时刻）合并进 `data/air_quality.db` 的
`air_quality_hourly.aqhi` 列 —— 不补的话空气质量 Agent 的 `avg_aqhi` 恒为 null，
`aqhi_level` 永远是 unknown，决策与报告里会出现"AQHI数据不可用"。

AQHI 文件是"日期 × 小时 × 站点"的宽表，日期形如 2023-08-01，小时 01~24，
与 EPICDI 一致（当日 00:00 加 h 小时）。个别数值带 `*`（代表该站设备停用、
用了邻近站替代），解析时去掉。
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "air_quality.db"

BASE_URL = "https://www.aqhi.gov.hk/epd/ddata/html/history/{year}/{yearmonth}_Eng.csv"

#: AQHI 文件里的站名 → 数据库里的 station_name。
#: 多数只是大小写差别，但 Sha Tin → SHATIN 这种拼写差异必须显式写出来。
STATION_NAME_MAP = {
    "Central/Western": "CENTRAL/WESTERN",
    "Southern": "SOUTHERN",
    "Eastern": "EASTERN",
    "Kwun Tong": "KWUN TONG",
    "Sham Shui Po": "SHAM SHUI PO",
    "Kwai Chung": "KWAI CHUNG",
    "Tsuen Wan": "TSUEN WAN",
    "Tseung Kwan O": "TSEUNG KWAN O",
    "Yuen Long": "YUEN LONG",
    "Tuen Mun": "TUEN MUN",
    "Tung Chung": "TUNG CHUNG",
    "Tai Po": "TAI PO",
    "Sha Tin": "SHATIN",
    "North": "NORTH",
    "Tap Mun": "TAP MUN",
    "Causeway Bay": "CAUSEWAY BAY",
    "Central": "CENTRAL",
    "Mong Kok": "MONG KOK",
}


def download_month(year: int, month: int, session: requests.Session | None = None,
                   retries: int = 3) -> str:
    """下载某个月的 AQHI CSV，返回文本（文件不存在时返回空串）。

    批量拉六年的实测经验：拉到几十个月时远端偶尔会直接断连
    （RemoteDisconnected），整批跑到一半就崩。这里按月重试几次，
    失败就跳过该月 —— 脚本是幂等的 UPDATE，补跑一遍就能填上缺口。
    """
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", "Mozilla/5.0")
    http.trust_env = False  # 绕过系统代理，否则连不上政府站点

    url = BASE_URL.format(year=year, yearmonth=f"{year}{month:02d}")
    for attempt in range(retries):
        try:
            response = http.get(url, timeout=120)
            if response.status_code != 200:
                return ""
            return response.content.decode("utf-8-sig")
        except requests.RequestException as exc:
            if attempt == retries - 1:
                print(f"  [skip] {year}-{month:02d} 拉取失败：{exc}")
                return ""
    return ""


def parse_month(text: str) -> list[tuple[str, str, float]]:
    """解析成 (station_name, time, aqhi) 三元组列表。"""
    if not text:
        return []

    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("Date,Hour,")), None
    )
    if header_index is None:
        return []

    reader = csv.reader(io.StringIO("\n".join(lines[header_index:])))
    header = next(reader)
    # 第一列是日期，第二列是小时，其余各列是站名
    column_station = [STATION_NAME_MAP.get(name.strip()) for name in header[2:]]

    rows: list[tuple[str, str, float]] = []
    current_date = ""
    for record in reader:
        if not record:
            continue
        # 日期列可能为空（同一天连续 24 行只在第一行写日期）
        if len(record) > 0 and record[0].strip():
            current_date = record[0].strip()
        if not current_date or len(record) < 3:
            continue

        hour_text = record[1].strip()
        if not hour_text.isdigit():
            continue
        try:
            stamp = datetime.strptime(current_date, "%Y-%m-%d") + timedelta(hours=int(hour_text))
        except ValueError:
            continue
        stamp_text = stamp.strftime("%Y-%m-%d %H:%M:%S")

        for offset, station in enumerate(column_station):
            if not station:
                continue
            value = record[offset + 2].strip() if offset + 2 < len(record) else ""
            value = value.replace("*", "").strip()  # * 表示用邻近站替代，数值仍可用
            if not value:
                continue
            try:
                rows.append((station, stamp_text, float(value)))
            except ValueError:
                continue
    return rows


def merge_into_db(conn: sqlite3.Connection, rows: list[tuple[str, str, float]]) -> int:
    """把 AQHI 更新到已入库的小时记录上。"""
    cursor = conn.executemany(
        "UPDATE air_quality_hourly SET aqhi = ? WHERE station_name = ? AND time = ?",
        [(aqhi, station, time) for station, time, aqhi in rows],
    )
    conn.commit()
    return cursor.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="AQHI 月度数据下载并合并进 air_quality.db")
    parser.add_argument("--start", default="2018-01", help="起始月份 YYYY-MM")
    parser.add_argument("--end", default="2024-12", help="结束月份 YYYY-MM")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m")
    end = datetime.strptime(args.end, "%Y-%m")

    conn = sqlite3.connect(args.db)
    session = requests.Session()
    session.headers.setdefault("User-Agent", "Mozilla/5.0")
    session.trust_env = False

    cursor = start
    total = 0
    while cursor <= end:
        text = download_month(cursor.year, cursor.month, session)
        rows = parse_month(text)
        touched = merge_into_db(conn, rows) if rows else 0
        total += touched
        print(f"[{cursor.year}-{cursor.month:02d}] 解析 {len(rows):6d} 条，更新 {touched:6d} 行")
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    covered = conn.execute("SELECT COUNT(*) FROM air_quality_hourly WHERE aqhi IS NOT NULL").fetchone()[0]
    print(f"[ok] 累计更新 {total} 次；库里有 AQHI 的记录 {covered} 行")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
