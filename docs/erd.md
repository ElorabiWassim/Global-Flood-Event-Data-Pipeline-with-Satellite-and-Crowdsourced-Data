# PostGIS Entity-Relationship Diagram

This ERD documents every table in `db/schema.sql` — the canonical source of
truth for the warehouse. GitHub renders the Mermaid block below inline.

## Schema overview

```
raw schema      -> 6 ingestion tables + 1 audit log
staging schema  -> 2 canonical tables (flood_events, social_flood_signals)
marts schema    -> 8 views built from staging (see transformations/marts.py)
```

## Full ERD (Mermaid)

```mermaid
erDiagram
    %% =========================================================
    %% RAW SCHEMA — original payloads preserved as JSONB
    %% =========================================================
    raw_dartmouth_events {
        BIGSERIAL id PK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_glofas_events {
        BIGSERIAL id PK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_copernicus_ems_events {
        BIGSERIAL id PK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_emdat_events {
        BIGSERIAL id PK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_reliefweb_events {
        BIGSERIAL id PK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_social_media_posts {
        BIGSERIAL id PK
        TEXT platform UK
        TEXT post_id UK
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT batch_id
        TIMESTAMPTZ ingested_at
        JSONB payload
    }

    raw_ingestion_log {
        BIGSERIAL id PK
        TEXT batch_id
        TEXT source
        TEXT source_url
        TEXT file_path
        TEXT file_checksum
        INTEGER rows_ingested
        TEXT status
        TEXT message
        TIMESTAMPTZ started_at
        TIMESTAMPTZ finished_at
    }

    %% =========================================================
    %% STAGING SCHEMA — canonical unified model
    %% =========================================================
    staging_flood_events {
        SERIAL id PK
        TEXT source UK
        TEXT source_event_id UK
        TEXT event_name
        TEXT main_cause
        TIMESTAMP date_start
        TIMESTAMP date_end
        TEXT country
        TEXT river_basin
        DOUBLE latitude
        DOUBLE longitude
        GEOMETRY_Point_4326 geometry
        INTEGER deaths
        INTEGER displaced
        INTEGER affected
        DOUBLE severity
        DOUBLE flood_impact_index
        TEXT glide_number
        TEXT url
        TEXT h3_index
        TIMESTAMPTZ loaded_at
    }

    staging_social_flood_signals {
        BIGSERIAL id PK
        TEXT platform UK
        TEXT post_id UK
        TEXT source_event_id FK
        TIMESTAMPTZ created_at
        TIMESTAMPTZ ingested_at
        TEXT text
        TEXT language
        TEXT url
        TEXT author_id_hash
        TEXTARRAY matched_keywords
        TEXTARRAY excluded_keywords
        DOUBLE flood_relevance_score
        DOUBLE location_confidence
        DOUBLE signal_confidence
        TEXT place_name
        TEXT country
        DOUBLE latitude
        DOUBLE longitude
        GEOMETRY_Point_4326 geometry
        TEXT h3_index
        JSONB raw_payload
        TIMESTAMPTZ loaded_at
    }

    %% =========================================================
    %% LINEAGE (logical, not declared FK — JSONB feeds normalization)
    %% =========================================================
    raw_dartmouth_events        ||--o{ staging_flood_events           : "normalize"
    raw_glofas_events           ||--o{ staging_flood_events           : "normalize"
    raw_copernicus_ems_events   ||--o{ staging_flood_events           : "normalize"
    raw_emdat_events            ||--o{ staging_flood_events           : "normalize"
    raw_reliefweb_events        ||--o{ staging_flood_events           : "normalize"
    raw_social_media_posts      ||--o{ staging_social_flood_signals   : "filter+score"

    %% Cross-staging join (used by marts.flood_events_with_social_signals)
    staging_flood_events        ||--o{ staging_social_flood_signals   : "h3+time join"
```

## Notes on the diagram

- **JSONB → canonical normalization**: the lineage arrows from `raw.*` to
  `staging.flood_events` are *logical*, not declared foreign keys. Each
  raw row's JSONB `payload` is parsed by a source-specific normalizer in
  [transformations/transform.py](../transformations/transform.py), which
  upserts into `staging.flood_events` keyed by
  `(source, source_event_id)`.
- **Social link**: `staging.social_flood_signals` has a logical FK to
  `raw.social_media_posts` via the
  `(platform, post_id)` natural key — enforced by DQ check #15
  (`social_orphan_staging_signals`).
- **Spatial join**: the mart view
  `marts.flood_events_with_social_signals` joins
  `staging.flood_events` to `staging.social_flood_signals` on
  `h3_index` plus a temporal window.

## PostGIS / geometry specifics

| Column | Type | Notes |
|---|---|---|
| `staging.flood_events.geometry` | `GEOMETRY(Point, 4326)` | WGS-84 lat/lon, GiST index (`ix_flood_events_geom`) |
| `staging.social_flood_signals.geometry` | `GEOMETRY(Point, 4326)` | WGS-84 lat/lon, GiST index (`ix_social_flood_signals_geom`) |
| `*.h3_index` | `TEXT` | Uber H3 resolution 7 (~5 km hex edge) |

## Indexes (all defined in [db/schema.sql](../db/schema.sql))

**`staging.flood_events`**: `country`, `river_basin`, `date_start`,
`severity`, `h3_index`, `geometry` (GiST).

**`staging.social_flood_signals`**: `platform`, `created_at`, `country`,
`h3_index`, `signal_confidence`, `geometry` (GiST).

**`raw.social_media_posts`**: `platform`, `ingested_at`.

## Rendering as PNG / SVG (optional)

If you need a raster image for the report appendix:

```bash
# Option A: Mermaid CLI
npm install -g @mermaid-js/mermaid-cli
mmdc -i docs/erd.md -o docs/erd.png -t neutral -b transparent

# Option B: paste the DBML in docs/erd.dbml into https://dbdiagram.io
#          then Export -> PNG/PDF
```
