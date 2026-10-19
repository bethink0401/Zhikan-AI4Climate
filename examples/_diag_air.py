import sqlite3
conn = sqlite3.connect("data/air_quality.db")

# 找出所有不符合 YYYY-MM-DD 格式的 time 值（月份 > 12 说明日月颠倒）
bad = conn.execute("""
    SELECT time, COUNT(*) as n
    FROM air_quality_hourly
    WHERE CAST(substr(time, 6, 2) AS INTEGER) > 12
    GROUP BY substr(time, 1, 7)
    ORDER BY time
    LIMIT 20
""").fetchall()
print("Malformed time samples (month>12 = day/month swapped):")
for r in bad:
    print(" ", r[0], "count:", r[1])

# 对比：2023-08-15 查到 0 行，但换成日月颠倒后 2023-15-08 有数据吗？
flipped = conn.execute(
    "SELECT COUNT(*) FROM air_quality_hourly"
    " WHERE UPPER(station_name) LIKE '%MONG KOK%'"
    " AND time LIKE '2023-15-08%'"
).fetchone()[0]
print("\n'2023-15-08%' (flipped) rows:", flipped)

conn.close()
