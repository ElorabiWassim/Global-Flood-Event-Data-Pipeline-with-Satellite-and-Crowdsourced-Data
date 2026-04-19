flood_events (
id SERIAL PRIMARY KEY,
-- Source tracking
source TEXT NOT NULL,
source_event_id TEXT,

-- Basic info
event_name TEXT,
main_cause TEXT,

-- Time
date_start TIMESTAMP NOT NULL,
date_end TIMESTAMP,

-- Location
country TEXT,
latitude DOUBLE PRECISION,
longitude DOUBLE PRECISION,
geometry GEOMETRY(Point, 4326),

-- Impact
deaths INTEGER,
displaced INTEGER,
affected INTEGER,

-- Severity / metrics
severity DOUBLE PRECISION,
flood_impact_index DOUBLE PRECISION,

-- External references
glide_number TEXT,
url TEXT,

-- Spatial indexing
h3_index TEXT
);