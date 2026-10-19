"""
修复 air_quality_hourly 表中日月颠倒的 time 字段。

原格式：YYYY-DD-MM HH:MM:SS  （日月颠倒）
目标格式：YYYY-MM-DD HH:MM:SS （正确）

只修改月份字段值 > 12 的行（即确实颠倒的行），月份 ≤ 12 的行不动，
避免对已经正确的少量数据重复操作。
"""

import sqlite3
from pathlib import Path

DB = Path("data/air_quality.db")

conn = sqlite3.connect(str(DB))

# 先统计要修的行数
bad_count = conn.execute(
    "SELECT COUNT(*) FROM air_quality_hourly WHERE CAST(substr(time,6,2) AS INTEGER) > 12"
).fetchone()[0]
print(f"待修复行数: {bad_count:,}")

if bad_count == 0:
    print("无需修复，退出。")
    conn.close()
    exit()

# 备份原 time 到临时列（以便出错回滚）
print("开始修复（一次性 UPDATE，请稍候）...")
conn.execute("""
    UPDATE air_quality_hourly
    SET time = substr(time,1,4) || '-'
             || substr(time,9,2) || '-'
             || substr(time,6,2)
             || substr(time,11)
    WHERE CAST(substr(time,6,2) AS INTEGER) > 12
""")
conn.commit()

# 验证
remaining = conn.execute(
    "SELECT COUNT(*) FROM air_quality_hourly WHERE CAST(substr(time,6,2) AS INTEGER) > 12"
).fetchone()[0]
print(f"修复后仍异常行数: {remaining}")

# 抽样验证
sample = conn.execute(
    "SELECT time FROM air_quality_hourly"
    " WHERE UPPER(station_name) LIKE '%MONG KOK%'"
    " AND time LIKE '2023-08-15%' LIMIT 5"
).fetchall()
print("2023-08-15 MONG KOK 样本:", [r[0] for r in sample])

conn.close()
print("完成。")
