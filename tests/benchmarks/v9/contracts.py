# -*- coding: utf-8 -*-
"""V9.6 自动化数据契约检查。

背景：本项目反复出现"静默失效"类缺陷——同一个错误模式已经出现三次：
  1. `open_db(DEFAULT_DB)` 默认参数在定义时绑定 → 替换模块变量无效；
  2. `AirQualityAgent.__init__(db_path=DEFAULT_DB)` 同样在导入时固化真实库路径
     → 合成世界的实验实际读真实库（标签与观测错配，结论无效）；
  3. NOx 门控查询站点名大小写不匹配 → 特征恒为 None 且异常被吞掉。

这些缺陷都不会抛错，只会让数字变得"看起来合理但无意义"。因此本模块把
"数据来源与特征完整性"变成**硬断言**：不满足直接 `ContractViolation`，
而不是静默继续跑完整个实验。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class ContractViolation(RuntimeError):
    """数据契约被破坏——实验必须失败，不得静默继续。"""


@contextmanager
def db_switch(db_path: Path, *, p90_cache: Path | None = None) -> Iterator[list]:
    """把整条管道切到指定库，并**校验每一次连接都指向它**。

    会 patch `tools.air_quality_tools.open_db / DEFAULT_DB` 与
    `agents.air_quality_agent.open_db / DEFAULT_DB`，同时记录每次实际使用的
    路径；退出时若有任何一次连接到别的库，抛 `ContractViolation`。
    """
    import sqlite3 as _sq

    import agents.air_quality_agent as _aqa
    import tools.air_quality_tools as aqt

    target = Path(db_path).resolve()
    used: list[Path] = []

    def _open(_path=target):
        resolved = Path(_path).resolve()
        used.append(resolved)
        conn = _sq.connect(str(resolved))
        conn.row_factory = _sq.Row
        return conn

    real = {"aqt_open": aqt.open_db, "aqt_db": aqt.DEFAULT_DB,
            "aqt_p90": getattr(aqt, "_P90_CACHE_PATH", None),
            "aqa_open": _aqa.open_db, "aqa_db": _aqa.DEFAULT_DB}
    aqt.open_db = _open
    aqt.DEFAULT_DB = target
    if p90_cache is not None:
        aqt._P90_CACHE_PATH = Path(p90_cache)
    _aqa.open_db = _open
    _aqa.DEFAULT_DB = target
    try:
        yield used
    finally:
        aqt.open_db, aqt.DEFAULT_DB = real["aqt_open"], real["aqt_db"]
        _aqa.open_db, _aqa.DEFAULT_DB = real["aqa_open"], real["aqa_db"]
        if real["aqt_p90"] is not None:
            aqt._P90_CACHE_PATH = real["aqt_p90"]
        wrong = [p for p in used if p != target]
        if wrong:
            raise ContractViolation(
                f"数据库契约被破坏：目标库 {target}，实际连接 {sorted(set(wrong))}")


def require_stations_subset(conn, station_names: list[str], context: str) -> None:
    """断言库里的站点全部落在预期集合内（防止连到别的库）。"""
    actual = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT station_name FROM air_quality_hourly")}
    unexpected = actual - set(station_names)
    if unexpected:
        raise ContractViolation(
            f"{context}：库中出现非预期站点 {sorted(unexpected)[:5]}"
            f"（预期集合共 {len(station_names)} 个）——很可能连到了错误的库")


def require_agent_result_complete(result: dict[str, Any], *, context: str,
                                 required_fields: tuple[str, ...],
                                 allow_none: tuple[str, ...] = ()) -> None:
    """断言 agent 的关键特征没有整批缺失（防止特征静默全空）。"""
    payload = result.get("result") or {}
    missing = [f for f in required_fields
               if f not in payload or (payload.get(f) is None and f not in allow_none)]
    if missing:
        raise ContractViolation(
            f"{context}：关键特征缺失 {missing}（data_quality="
            f"{result.get('data_quality')}）——特征管道可能静默失效")


def require_no_all_null(rows: list[dict[str, Any]], fields: tuple[str, ...],
                        *, context: str) -> None:
    """断言某批样本里不存在"整列全空"的特征。"""
    for field in fields:
        values = [r.get(field) for r in rows]
        if values and all(v is None for v in values):
            raise ContractViolation(
                f"{context}：特征 `{field}` 在 {len(values)} 条样本上全为空")


def require_stats_consistent(point: float, ci_mean: float, *, context: str,
                            tol: float = 1e-4) -> None:
    """断言统计口径一致：CI 的 mean_diff 必须等于点估计差。"""
    if abs(point - ci_mean) > tol:
        raise ContractViolation(
            f"{context}：统计口径不一致——点估计差 {point:.6f} vs CI 均值 "
            f"{ci_mean:.6f}（容差 {tol}）；两者必须是同一个统计量")


def require_db_readable(path: Path, min_rows: int = 1) -> None:
    """断言目标库可读且非空。"""
    if not Path(path).exists():
        raise ContractViolation(f"目标库不存在：{path}")
    with sqlite3.connect(str(path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM air_quality_hourly").fetchone()[0]
    if n < min_rows:
        raise ContractViolation(f"目标库行数不足：{path} 只有 {n} 行")
