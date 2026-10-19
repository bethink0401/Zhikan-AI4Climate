-- Air Quality Agent 数据表（SQLite）

CREATE TABLE IF NOT EXISTS air_quality_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    station_name TEXT NOT NULL,
    station_type TEXT NOT NULL DEFAULT 'general',
    time TEXT NOT NULL,
    aqhi REAL,
    no2 REAL,
    nox REAL,
    pm25 REAL,
    pm10 REAL,
    o3 REAL,
    so2 REAL,
    co REAL,
    source TEXT DEFAULT 'epd',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_aqh_station_time
    ON air_quality_hourly (station_id, time);

CREATE INDEX IF NOT EXISTS idx_aqh_time
    ON air_quality_hourly (time);

CREATE INDEX IF NOT EXISTS idx_aqh_station_name
    ON air_quality_hourly (station_name);

CREATE TABLE IF NOT EXISTS air_quality_stations (
    station_id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    station_type TEXT NOT NULL DEFAULT 'general',
    lat REAL,
    lon REAL,
    district TEXT,
    is_roadside INTEGER DEFAULT 0,
    source TEXT DEFAULT 'epd'
);
