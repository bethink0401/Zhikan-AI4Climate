# -*- coding: utf-8 -*-
"""
香港 ATC 交通统计压缩包 ETL
- 自动解压外层 zip / 年份 zip
- 解析 Core Station .xls（LINK 元数据 + OCC 车型 Pro./Ocp.）
- 宽表转长表并写入 SQLite：traffic_vehicle_mix / traffic_station_meta
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

import xlrd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "traffic.db"
SCHEMA_SQL = Path(__file__).resolve().parent / "schema_traffic.sql"
DEFAULT_EXTRACT = ROOT / "data" / "raw_atc"

# OCC 表头两行合并后的标准车型名（列索引 2..11）
VEHICLE_TYPES = [
    "Motor Cycle",
    "Private Car",
    "Taxi",
    "Private LB",
    "PLB",
    "Goods Vehicle Light",
    "Goods Vehicle M&H",
    "Non Franchised Bus",
    "Franchised Bus SD",
    "Franchised Bus DD",
]


def init_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # data/*.db 在版本库里走 Git LFS。若只 clone 没拉 LFS，本地是一个文本指针文件，
    # sqlite3 连上去后每条语句都会报 "file is not a database"，这里直接重建。
    if db_path.is_file() and db_path.read_bytes()[:15] != b"SQLite format 3":
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    # 兼容旧版单字段主键：重建 meta 表（atc_station_id, year）
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='traffic_station_meta'"
    ).fetchone()
    if row and "PRIMARY KEY (atc_station_id, year)" not in (row[0] or ""):
        conn.execute("DROP TABLE IF EXISTS traffic_station_meta")
        conn.commit()
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def extract_atc_archive(zip_path: Path, out_dir: Path, years: Optional[list[int]] = None) -> Path:
    """解压 ATC_TRAFFIC_DATA.zip，并按需展开年份 zip。返回数据根目录。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)

    base = out_dir / "ATC_TRAFFIC_DATA"
    if not base.exists():
        cands = list(out_dir.glob("**/ATC_TRAFFIC_DATA"))
        base = cands[0] if cands else out_dir

    for yzip in sorted(base.glob("*.zip")):
        m = re.search(r"(20\d{2})", yzip.stem)
        if not m:
            continue
        year = int(m.group(1))
        if years and year not in years:
            continue
        target = base / str(year)
        if (target / "Current").exists():
            print(f"[skip-extract] year={year} already at {target}")
            continue

        with zipfile.ZipFile(yzip) as yf:
            names = yf.namelist()
            # 2023/2024：zip 内自带 {year}/Current/...
            # 2018–2022：zip 根下直接是 Current/...，需解到 base/{year}/
            has_year_prefix = any(
                n.replace("\\", "/").startswith(f"{year}/") for n in names
            )
            if has_year_prefix:
                yf.extractall(base)
            else:
                target.mkdir(parents=True, exist_ok=True)
                yf.extractall(target)
        print(f"[extract] year={year} -> {target}")
    return base


def _cell_str(sh, r: int, c: int) -> str:
    v = sh.cell_value(r, c)
    if v in ("", None):
        return ""
    return str(v).strip()


def parse_station_meta(xls_path: Path) -> dict[str, Any]:
    book = xlrd.open_workbook(str(xls_path))
    sh = book.sheet_by_index(0)
    year = sh.cell_value(0, 17)
    station_id = sh.cell_value(1, 17)
    network = sh.cell_value(2, 17)
    road_type = sh.cell_value(3, 17)
    link = ""
    for c in range(sh.ncols):
        if _cell_str(sh, 0, c).upper() == "LINK":
            for c2 in range(c + 1, sh.ncols):
                vv = sh.cell_value(0, c2)
                if vv not in ("", None):
                    link = str(vv).strip()
                    break
            break
    sid = str(int(station_id)) if isinstance(station_id, float) else str(station_id).strip()
    y = int(year) if year not in ("", None) else None
    return {
        "atc_station_id": sid,
        "year": y,
        "road_link": link,
        "road_network": str(network).strip() if network not in ("", None) else "",
        "road_type": str(road_type).strip() if road_type not in ("", None) else "",
        "source_file": str(xls_path),
    }


def _normalize_time_slot(raw: str) -> str:
    s = re.sub(r"\s+", "", str(raw))
    m = re.match(r"(\d{4})-(\d{4})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if "16hour" in s.lower() or s.lower().startswith("16hours"):
        return "16hours"
    return s


def parse_occ_long(xls_path: Path, meta: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """将 OCC 宽表转为长表记录。"""
    book = xlrd.open_workbook(str(xls_path))
    occ_name = next((n for n in book.sheet_names() if "OCC" in n.upper()), None)
    if not occ_name:
        return []
    sh = book.sheet_by_name(occ_name)
    meta = meta or parse_station_meta(xls_path)

    day_type = "Mon-Fri"
    title = _cell_str(sh, 2, 0).lower()
    if "saturday" in title:
        day_type = "Saturday"
    elif "sunday" in title:
        day_type = "Sunday"

    rows: list[dict[str, Any]] = []
    r = 7
    while r < sh.nrows:
        time_raw = _cell_str(sh, r, 0)
        metric = _cell_str(sh, r, 1).lower()
        if not time_raw and metric in ("pro", "ocp"):
            # 延续上一时段（部分表 Ocp 行时间为空）
            pass
        if metric not in ("pro", "ocp"):
            # 跳过图例等
            if "legend" in time_raw.lower() or "ocp." in time_raw.lower():
                break
            r += 1
            continue

        # 找成对的 Pro/Ocp
        if metric == "pro":
            time_slot = _normalize_time_slot(time_raw)
            if time_slot in ("", "Peakhour"):
                r += 1
                continue
            pro_vals = [sh.cell_value(r, c) for c in range(2, 12)]
            ocp_vals = [None] * 10
            if r + 1 < sh.nrows and _cell_str(sh, r + 1, 1).lower() == "ocp":
                ocp_vals = [sh.cell_value(r + 1, c) for c in range(2, 12)]
                next_r = r + 2
            else:
                next_r = r + 1

            for i, vtype in enumerate(VEHICLE_TYPES):
                pro = pro_vals[i]
                ocp = ocp_vals[i]
                try:
                    pro_f = float(pro) if pro not in ("", None) else None
                except (TypeError, ValueError):
                    pro_f = None
                try:
                    ocp_f = float(ocp) if ocp not in ("", None) else None
                except (TypeError, ValueError):
                    ocp_f = None
                rows.append(
                    {
                        "year": meta.get("year"),
                        "region_or_station": meta.get("road_link") or meta.get("atc_station_id"),
                        "atc_station_id": meta.get("atc_station_id"),
                        "road_link": meta.get("road_link"),
                        "day_type": day_type,
                        "time_slot": time_slot,
                        "vehicle_type": vtype,
                        "proportion": pro_f,
                        "occupancy": ocp_f,
                        "source_file": str(xls_path.name),
                    }
                )
            r = next_r
        else:
            r += 1
    return rows


def iter_station_xls(year_dir: Path) -> Iterable[Path]:
    current = year_dir / "Current"
    if not current.exists():
        current = year_dir
    for p in sorted(current.glob("S*.xls")):
        if re.fullmatch(r"S\d+", p.stem):
            yield p


def import_year(
    conn: sqlite3.Connection,
    year_dir: Path,
    station_ids: Optional[set[str]] = None,
    replace_year: bool = True,
) -> int:
    year_m = re.search(r"(20\d{2})", year_dir.name)
    year = int(year_m.group(1)) if year_m else None
    if replace_year and year is not None:
        conn.execute("DELETE FROM traffic_vehicle_mix WHERE year = ?", (year,))
        conn.execute("DELETE FROM traffic_station_meta WHERE year = ?", (year,))

    n = 0
    meta_rows = []
    mix_rows = []
    for xls in iter_station_xls(year_dir):
        try:
            meta = parse_station_meta(xls)
        except Exception as e:
            print(f"[warn] meta fail {xls.name}: {e}")
            continue
        if station_ids and meta["atc_station_id"] not in station_ids:
            continue
        if year and not meta.get("year"):
            meta["year"] = year
        meta_rows.append(meta)
        try:
            long_rows = parse_occ_long(xls, meta)
        except Exception as e:
            print(f"[warn] occ fail {xls.name}: {e}")
            continue
        mix_rows.extend(long_rows)
        n += 1
        if n % 40 == 0:
            print(f"  ... parsed {n} stations")

    conn.executemany(
        """
        INSERT OR REPLACE INTO traffic_station_meta
        (atc_station_id, year, road_link, road_network, road_type, source_file)
        VALUES (:atc_station_id, :year, :road_link, :road_network, :road_type, :source_file)
        """,
        meta_rows,
    )
    conn.executemany(
        """
        INSERT INTO traffic_vehicle_mix
        (year, region_or_station, atc_station_id, road_link, day_type, time_slot,
         vehicle_type, proportion, occupancy, source_file)
        VALUES
        (:year, :region_or_station, :atc_station_id, :road_link, :day_type, :time_slot,
         :vehicle_type, :proportion, :occupancy, :source_file)
        """,
        mix_rows,
    )
    conn.commit()
    print(f"[ok] year_dir={year_dir.name} stations={n} mix_rows={len(mix_rows)}")
    return len(mix_rows)


def run_etl(
    zip_path: Path,
    db_path: Path = DEFAULT_DB,
    years: Optional[list[int]] = None,
    station_ids: Optional[list[str]] = None,
    extract_dir: Path = DEFAULT_EXTRACT,
) -> Path:
    years = years or [2023]
    base = extract_atc_archive(zip_path, extract_dir, years=years)
    conn = init_db(db_path)
    sid_set = set(station_ids) if station_ids else None
    for y in years:
        ydir = base / str(y)
        if not ydir.exists():
            print(f"[skip] missing year dir {ydir}")
            continue
        import_year(conn, ydir, station_ids=sid_set)
    conn.close()
    return db_path


def main():
    parser = argparse.ArgumentParser(description="ATC 交通数据 ETL → SQLite")
    parser.add_argument(
        "--zip",
        default=r"c:\Users\mistakez\Downloads\ATC_TRAFFIC_DATA.zip",
        help="ATC_TRAFFIC_DATA.zip 路径",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--years", default="2018,2019,2020,2021,2022,2023,2024", help="逗号分隔年份")
    parser.add_argument(
        "--stations",
        default="",
        help="可选：仅导入指定 ATC 站，如 3006,1029,1030；默认全站",
    )
    parser.add_argument("--all-stations", action="store_true", help="导入该年全部站点（默认行为）")
    parser.add_argument("--case-stations", action="store_true", help="仅导入案例站（更快）")
    args = parser.parse_args()

    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    default_case = ["3006", "4204", "3015", "1029", "2214", "1030", "2207", "2208", "2209"]
    if args.case_stations and not args.stations.strip():
        stations = default_case
    elif args.stations.strip():
        stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    else:
        stations = None  # 全站（满足 2018—2024 批量入库）

    db = run_etl(Path(args.zip), Path(args.db), years=years, station_ids=stations)
    print(f"DB ready: {db}")


if __name__ == "__main__":
    main()
