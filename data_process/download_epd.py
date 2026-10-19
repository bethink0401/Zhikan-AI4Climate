# -*- coding: utf-8 -*-
"""
EPD 空气质量数据下载与入库（香港环保署 EPICDI）。

数据源：https://cd.epic.epd.gov.hk/EPICDI/air/station/
内容是各空气质素监测站的每小时污染物浓度（SO2 / NOX / NO2 / CO / RSP / O3 / FSP），
写入 data/air_quality.db 的 air_quality_hourly 表。

这个站点是一个 JSF（Jakarta Faces / MyFaces）应用，没有 REST 接口，必须模拟表单：

1. GET 页面，从隐藏字段 `jakarta.faces.ViewState` 里取当前视图状态；
2. POST 回同一地址，带上 requestType=download / timerange=hourly / 日期区间 /
   污染物与站点复选框；
3. **关键是 `form:_idcl` 这个隐藏字段**。"Download CSV" 是一个 HTML `<a>`，它的
   onclick 调 `myfaces.oam.submitForm('form', 'excel_by_station')`，而这个函数
   （见 faces.js 的 oam 模块）会往表单里塞两个隐藏字段：
       form:_idcl = <按钮名>
       <其余自定义参数>
   少了 `form:_idcl`，服务器根本不认为有动作被触发，会原样重渲染整张表单
   —— HTTP 200、Content-Type: text/html、字节数和 GET 完全一致，
   看起来像"提交没反应"，非常容易误判成网络或参数问题。

实测 2023 全年（18 站 × 8760 小时 = 157 692 行）耗时约 41 s。
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "air_quality.db"
SCHEMA_SQL = ROOT / "data_process" / "schema_air_quality.sql"

BASE_URL = "https://cd.epic.epd.gov.hk/EPICDI/air/station/"

#: EPICDI 的列名 → 数据库列名。RSP = 可吸入悬浮粒子（PM10），FSP = 微细悬浮粒子（PM2.5）。
POLLUTANT_MAP = {
    "SO2": "so2",
    "NOX": "nox",
    "NO2": "no2",
    "CO": "co",
    "RSP": "pm10",
    "O3": "o3",
    "FSP": "pm25",
}

#: 路边站。EPD 的三座路边监测站，与 data/station_mapping.json 一致。
ROADSIDE_STATIONS = {"CAUSEWAY BAY", "CENTRAL", "MONG KOK"}

#: 站点坐标。EPD 站点页没有坐标，这里用 EPD 公开的监测站位置登记。
#: 只用于写 air_quality_stations，数值取自已公开的监测站经纬度。
STATION_COORDS = {
    "CENTRAL/WESTERN": (22.2867, 114.1400),
    "EASTERN": (22.2819, 114.2194),
    "KWAI CHUNG": (22.3622, 114.1270),
    "KWUN TONG": (22.3130, 114.2260),
    "NORTH": (22.4960, 114.1370),
    "SHAM SHUI PO": (22.3310, 114.1630),
    "SHATIN": (22.3780, 114.1850),
    "SOUTHERN": (22.2460, 114.1590),
    "TAI PO": (22.4510, 114.1680),
    "TAP MUN": (22.4710, 114.2700),
    "TSEUNG KWAN O": (22.3150, 114.2640),
    "TSUEN WAN": (22.3710, 114.1140),
    "TUEN MUN": (22.3930, 113.9720),
    "TUNG CHUNG": (22.2890, 113.9440),
    "YUEN LONG": (22.4450, 114.0220),
    "CAUSEWAY BAY": (22.2802, 114.1851),
    "CENTRAL": (22.2818, 114.1581),
    "MONG KOK": (22.3227, 114.1683),
}

#: 表单里“全站 / 全污染物”的复选框 id，来自页面本身（站点数变了也不用改）。
PARAM_CHECKBOX = "j_id_4a:{i}:parameters"
GENERAL_STATION_CHECKBOX = "j_id_4w:{i}:stations"
ROADSIDE_STATION_CHECKBOX = "j_id_59:{i}:stations"


def init_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """按 schema_air_quality.sql 建库（已存在则不动）。

    `data/*.db` 在版本库里是 Git LFS 托管的。如果只 clone 了仓库没拉 LFS，
    拿到的会是一个 100 多字节的文本指针文件 —— sqlite3 连上去以后所有语句都会
    报 "file is not a database"。这里先认一下文件头，是指针就删掉重建，
    否则每次都要人工先手动删文件。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.is_file() and db_path.read_bytes()[:15] != b"SQLite format 3":
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def _count_checkboxes(html: str, template: str) -> int:
    """数一数页面上某一组复选框有多少个，避免把站点数量写死在代码里。"""
    pattern = template.replace("{i}", r"(\d+)")
    return len({int(m) for m in re.findall(pattern, html)})


def download_hourly(
    start_date: str,
    end_date: str,
    session: requests.Session | None = None,
    timeout: int = 600,
) -> str:
    """下载指定日期区间的逐小时数据，返回 CSV 文本。

    日期格式为 YYYY-MM-DD，例如 download_hourly("2023-01-01", "2023-12-31")。
    """
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", "Mozilla/5.0")
    # 本机 curl / requests 会读到系统代理，这里显式绕过，否则连不上政府站点。
    http.trust_env = False

    page = http.get(BASE_URL + "?lang=en", timeout=60)
    page.raise_for_status()
    match = re.search(r'name="jakarta\.faces\.ViewState"[^>]*value="([^"]*)"', page.text)
    if not match:
        raise RuntimeError("EPICDI 页面结构变了：找不到 jakarta.faces.ViewState")

    form = {
        "form_SUBMIT": "1",
        "jakarta.faces.ViewState": match.group(1),
        "requestType": "download",
        "timerange": "hourly",
        "downloadHourlyFromDate": start_date.replace("-", "/"),
        "downloadHourlyToDate": end_date.replace("-", "/"),
        # ↓ 缺这一项服务器不会触发下载，只会把表单重渲染一遍
        "form:_idcl": "excel_by_station",
    }
    for i in range(_count_checkboxes(page.text, PARAM_CHECKBOX)):
        form[PARAM_CHECKBOX.format(i=i)] = "true"
    for i in range(_count_checkboxes(page.text, GENERAL_STATION_CHECKBOX)):
        form[GENERAL_STATION_CHECKBOX.format(i=i)] = "true"
    for i in range(_count_checkboxes(page.text, ROADSIDE_STATION_CHECKBOX)):
        form[ROADSIDE_STATION_CHECKBOX.format(i=i)] = "true"

    response = http.post(BASE_URL, data=form, timeout=timeout)
    response.raise_for_status()

    disposition = response.headers.get("Content-Disposition", "")
    if "attachment" not in disposition:
        raise RuntimeError(
            "EPICDI 没有返回附件（拿到的是 HTML 表单页面）。"
            "通常是 ViewState 失效或 form:_idcl 没带上。"
        )
    return response.content.decode("utf-8-sig")


def parse_hourly_csv(text: str) -> list[dict]:
    """把 EPICDI 的 CSV 解析成数据库行。

    文件前面有若干行 Remarks 说明，表头行以 `DATE,HOUR,STATION` 开头。
    HOUR 取 1~24，代表该小时的平均浓度；这里把 DAY 00:00 加上 HOUR 小时换算成时间戳，
    于是 HOUR=8 → 当日 08:00:00，HOUR=24 → 次日 00:00:00。
    """
    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("DATE,HOUR,STATION")), None
    )
    if header_index is None:
        raise RuntimeError("EPICDI CSV 里找不到表头行 DATE,HOUR,STATION")

    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    for raw in reader:
        station = (raw.get("STATION") or "").strip()
        date_text = (raw.get("DATE") or "").strip()
        hour_text = (raw.get("HOUR") or "").strip()
        if not station or not date_text or not hour_text:
            continue

        day, month, year = date_text.split("/")
        stamp = datetime(int(year), int(month), int(day)) + timedelta(hours=int(hour_text))

        record = {
            "station_id": station.lower().replace(" ", "_").replace("/", "_"),
            "station_name": station,
            "station_type": "roadside" if station in ROADSIDE_STATIONS else "general",
            "time": stamp.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for csv_name, column in POLLUTANT_MAP.items():
            value = (raw.get(csv_name) or "").strip()
            if value in ("", "N.A.", "N/A", "-"):
                record[column] = None
                continue
            try:
                record[column] = float(value)
            except ValueError:
                record[column] = None
        rows.append(record)
    return rows


def import_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """把小时记录写入 air_quality_hourly，同时维护 air_quality_stations。"""
    conn.executemany(
        """INSERT OR REPLACE INTO air_quality_hourly
           (station_id, station_name, station_type, time,
            aqhi, no2, nox, pm25, pm10, o3, so2, co, source)
           VALUES (:station_id, :station_name, :station_type, :time,
                   NULL, :no2, :nox, :pm25, :pm10, :o3, :so2, :co, 'epd_epicdi')""",
        rows,
    )

    stations = {}
    for row in rows:
        stations[row["station_name"]] = row["station_type"]
    for name, kind in stations.items():
        lat, lon = STATION_COORDS.get(name, (None, None))
        conn.execute(
            """INSERT OR REPLACE INTO air_quality_stations
               (station_id, station_name, station_type, lat, lon, district, is_roadside, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'epd_epicdi')""",
            (
                name.lower().replace(" ", "_").replace("/", "_"),
                name,
                kind,
                lat,
                lon,
                "",
                1 if kind == "roadside" else 0,
            ),
        )
    conn.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="EPD 逐小时空气质量数据下载（EPICDI）")
    parser.add_argument("--start", default="2023-01-01", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2023-12-31", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--csv-out", default="", help="可选：把原始 CSV 另存一份")
    args = parser.parse_args()

    print(f"[download] EPICDI 逐小时数据 {args.start} ~ {args.end}")
    text = download_hourly(args.start, args.end)
    if args.csv_out:
        Path(args.csv_out).write_text(text, encoding="utf-8")
        print(f"[saved] {args.csv_out}")

    rows = parse_hourly_csv(text)
    conn = init_db(Path(args.db))
    count = import_rows(conn, rows)
    conn.close()
    print(f"[ok] 写入 {count} 行 → {args.db}")


if __name__ == "__main__":
    sys.exit(main())
