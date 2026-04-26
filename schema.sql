-- ============================================================================
-- Global Flood Event Data Pipeline — Database schema
--
-- This file is the SOURCE OF TRUTH for the staging.flood_events table and
-- the schemas used by the pipeline:
--   * raw      : original records exactly as ingested (one row per source-row)
--   * staging  : cleaned / unified flood events (canonical model)
--   * marts    : analysis / API-ready tables and views
-- ============================================================================

-- ---------- Extensions (PostGIS is required, h3-pg is optional) -------------
CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------- Schemas ---------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;

-- ---------- Raw schema: one table per source --------------------------------
-- Every raw table preserves the original payload as JSONB plus ingestion
-- metadata so the load is fully reproducible and auditable.
CREATE TABLE IF NOT EXISTS raw.dartmouth_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    batch_id        TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.glofas_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    batch_id        TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.copernicus_ems_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    batch_id        TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.emdat_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    batch_id        TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.reliefweb_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    batch_id        TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL
);

-- ---------- Ingestion audit log (one row per ingestion run / source) -------
CREATE TABLE IF NOT EXISTS raw.ingestion_log (
    id              BIGSERIAL PRIMARY KEY,
    batch_id        TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT,
    file_path       TEXT,
    file_checksum   TEXT,
    rows_ingested   INTEGER,
    status          TEXT NOT NULL,           -- success | failure | skipped
    message         TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

-- ---------- Staging: unified flood events ----------------------------------
-- Canonical model. ONE row per ingested source event.
CREATE TABLE IF NOT EXISTS staging.flood_events (
    id                  SERIAL PRIMARY KEY,

    -- Source tracking
    source              TEXT NOT NULL,
    source_event_id     TEXT,

    -- Basic info
    event_name          TEXT,
    main_cause          TEXT,

    -- Time
    date_start          TIMESTAMP NOT NULL,
    date_end            TIMESTAMP,

    -- Location
    country             TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    geometry            GEOMETRY(Point, 4326),

    -- Impact
    deaths              INTEGER,
    displaced           INTEGER,
    affected            INTEGER,

    -- Severity / metrics
    severity            DOUBLE PRECISION,
    flood_impact_index  DOUBLE PRECISION,

    -- External references
    glide_number        TEXT,
    url                 TEXT,

    -- Spatial indexing (H3 hexagonal grid)
    h3_index            TEXT,

    -- Convenience: when row was last refreshed by the pipeline
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_flood_events_source UNIQUE (source, source_event_id)
);

-- Idempotent forward-migrations. CREATE TABLE IF NOT EXISTS does NOT add new
-- columns when an older table is already present, so we additively bring the
-- schema up to date here. Safe to re-run.
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS event_name         TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS main_cause         TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS country            TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS latitude           DOUBLE PRECISION;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS longitude          DOUBLE PRECISION;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS geometry           GEOMETRY(Point, 4326);
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS deaths             INTEGER;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS displaced          INTEGER;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS affected           INTEGER;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS severity           DOUBLE PRECISION;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS flood_impact_index DOUBLE PRECISION;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS glide_number       TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS url                TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS h3_index           TEXT;
ALTER TABLE staging.flood_events ADD COLUMN IF NOT EXISTS loaded_at          TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- The unique constraint (source, source_event_id) supports the upsert in
-- scripts/transform.py. ALTER TABLE has no IF NOT EXISTS for constraints,
-- so wrap in a DO-block.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_flood_events_source'
    ) THEN
        ALTER TABLE staging.flood_events
            ADD CONSTRAINT uq_flood_events_source UNIQUE (source, source_event_id);
    END IF;
END $$;

-- Indexes for typical query paths
CREATE INDEX IF NOT EXISTS ix_flood_events_country   ON staging.flood_events (country);
CREATE INDEX IF NOT EXISTS ix_flood_events_date      ON staging.flood_events (date_start);
CREATE INDEX IF NOT EXISTS ix_flood_events_severity  ON staging.flood_events (severity);
CREATE INDEX IF NOT EXISTS ix_flood_events_h3        ON staging.flood_events (h3_index);
CREATE INDEX IF NOT EXISTS ix_flood_events_geom      ON staging.flood_events USING GIST (geometry);