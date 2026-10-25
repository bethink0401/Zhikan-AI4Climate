PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS station (
    station_id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    station_name_zh TEXT,
    station_type TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    address TEXT
);

CREATE TABLE IF NOT EXISTS road_network (
    road_id TEXT PRIMARY KEY,
    object_id TEXT,
    street_code TEXT,
    road_name TEXT NOT NULL,
    road_name_zh TEXT,
    road_type TEXT,
    route_number TEXT,
    travel_direction TEXT,
    near_stations TEXT,
    geometry TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poi_sensitive (
    poi_id TEXT PRIMARY KEY,
    poi_type TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    name_zh TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    district TEXT,
    district_zh TEXT,
    address TEXT,
    address_zh TEXT
);

CREATE INDEX IF NOT EXISTS idx_poi_type ON poi_sensitive (poi_type);
CREATE INDEX IF NOT EXISTS idx_poi_lat_lon ON poi_sensitive (lat, lon);
