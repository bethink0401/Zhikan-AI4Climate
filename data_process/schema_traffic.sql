-- Traffic Agent 数据表（SQLite / 可迁移至 PostgreSQL）

CREATE TABLE IF NOT EXISTS traffic_vehicle_mix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    region_or_station TEXT NOT NULL,
    atc_station_id TEXT,
    road_link TEXT,
    day_type TEXT NOT NULL DEFAULT 'Mon-Fri',
    time_slot TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    proportion REAL,
    occupancy REAL,
    source_file TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tvm_year_station_slot
    ON traffic_vehicle_mix (year, region_or_station, time_slot);

CREATE INDEX IF NOT EXISTS idx_tvm_atc_slot
    ON traffic_vehicle_mix (atc_station_id, year, time_slot);

CREATE TABLE IF NOT EXISTS traffic_volume (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    road_id TEXT,
    road_name TEXT,
    station_id TEXT,
    time_slot TEXT,
    total_volume REAL,
    avg_speed REAL,
    road_occupancy REAL,
    source_file TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS traffic_station_meta (
    atc_station_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    road_link TEXT,
    road_network TEXT,
    road_type TEXT,
    source_file TEXT,
    PRIMARY KEY (atc_station_id, year)
);
