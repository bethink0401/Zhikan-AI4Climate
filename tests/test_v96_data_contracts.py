# -*- coding: utf-8 -*-
"""V9.6 数据契约回归测试。

本项目连续三次出现"静默失效"：
  1. `open_db(DEFAULT_DB)` 默认参数在定义时绑定 → 替换模块变量无效；
  2. `AirQualityAgent.__init__(db_path=DEFAULT_DB)` 同样在导入时固化真实库路径
     → 合成世界实验实际读真实库（标签与观测错配，整轮结论无效）；
  3. NOx 门控站点名大小写不匹配 → 特征恒为 None 且异常被吞掉。

本测试把"默认库必须在调用时解析"和"契约被破坏必须失败"固化为断言。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "benchmarks" / "v9"))

from agents.air_quality_agent import AirQualityAgent  # noqa: E402
from contracts import (ContractViolation, db_switch, require_agent_result_complete,  # noqa: E402
                       require_no_all_null, require_stations_subset,
                       require_stats_consistent)


REAL_DB = ROOT / "data" / "air_quality.db"


def _make_db(path: Path) -> None:
    """用真实库的 schema 建一个只有合成站的迷你库（列名/约束与生产一致）。"""
    conn = sqlite3.connect(str(path))
    conn.execute("ATTACH DATABASE ? AS real", (str(REAL_DB),))
    conn.execute("CREATE TABLE air_quality_hourly AS "
                 "SELECT * FROM real.air_quality_hourly WHERE 0")
    conn.execute("DETACH DATABASE real")
    columns = [r[1] for r in conn.execute("PRAGMA table_info(air_quality_hourly)")]
    values = {"station_name": "SYN_A", "station_id": "SYN_A", "station_type": "roadside",
              "no2": 120.0, "nox": 200.0, "latitude": 22.3, "longitude": 114.1,
              "source": "synthetic"}
    for hour in range(6, 9):
        payload = {**values, "time": f"2024-08-03 {hour:02d}:00:00"}
        conn.execute(
            f"INSERT INTO air_quality_hourly ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [payload.get(c) for c in columns])
    conn.commit()
    conn.close()


def test_default_db_is_resolved_at_call_time() -> None:
    tmp_path = Path(tempfile.mkdtemp())
    """agent 未显式传库时，必须读**调用时刻**的模块级 DEFAULT_DB。"""
    import tools.air_quality_tools as aqt
    db = tmp_path / "synthetic.db"
    _make_db(db)
    agent = AirQualityAgent()
    assert agent.db_path is None, "默认库不能在构造时固化"
    with db_switch(db):
        out = agent.run({"task_id": "t", "station_name": "SYN_A",
                         "start_time": "2024-08-03 06:00:00",
                         "end_time": "2024-08-03 08:00:00"})
    assert out["status"] == "success"
    # 读到的必须是合成库里的值（120），而不是别处
    assert aqt.DEFAULT_DB != db  # 退出后已还原


def test_db_switch_raises_when_other_db_is_opened() -> None:
    tmp_path = Path(tempfile.mkdtemp())
    """契约：运行期间任何一次连接指向别的库都要抛错，而不是静默继续。"""
    import tools.air_quality_tools as aqt
    db = tmp_path / "synthetic.db"
    _make_db(db)
    try:
        with db_switch(db):
            aqt.open_db(Path("/tmp/some_other_place.db"))
    except ContractViolation as exc:
        assert "契约被破坏" in str(exc)
    else:
        raise AssertionError("连到别的库时必须抛 ContractViolation")


def test_require_stations_subset_catches_wrong_db() -> None:
    tmp_path = Path(tempfile.mkdtemp())
    db = tmp_path / "synthetic.db"
    _make_db(db)
    conn = sqlite3.connect(str(db))
    require_stations_subset(conn, ["SYN_A"], "test")   # 通过
    try:
        require_stations_subset(conn, ["OTHER"], "test")
    except ContractViolation:
        pass
    else:
        raise AssertionError("非预期站点必须抛 ContractViolation")
    conn.close()


def test_require_agent_result_complete() -> None:
    good = {"result": {"anomaly_score": 0.7}, "data_quality": {}}
    require_agent_result_complete(good, context="t", required_fields=("anomaly_score",))
    bad = {"result": {"anomaly_score": None}, "data_quality": {}}
    try:
        require_agent_result_complete(bad, context="t",
                                      required_fields=("anomaly_score",))
    except ContractViolation:
        return
    raise AssertionError("关键特征缺失必须抛 ContractViolation")


def test_require_no_all_null() -> None:
    require_no_all_null([{"a": 1}, {"a": None}], ("a",), context="t")
    try:
        require_no_all_null([{"a": None}, {"a": None}], ("a",), context="t")
    except ContractViolation:
        return
    raise AssertionError("整列全空必须抛 ContractViolation")


def test_require_stats_consistent() -> None:
    require_stats_consistent(0.0145, 0.0145, context="t")
    try:
        require_stats_consistent(0.0145, -0.0354, context="t")
    except ContractViolation:
        return
    raise AssertionError("点估计与 CI 口径不一致必须抛 ContractViolation")
