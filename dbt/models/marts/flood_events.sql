-- Optional dbt mart pointing to the canonical staging.flood_events table.
-- The Python pipeline already creates the full marts schema; this model
-- exists so dbt-only consumers can also see a unified view.
{{ config(materialized='view', schema='marts') }}

SELECT
    id,
    source,
    source_event_id,
    event_name,
    main_cause,
    date_start,
    date_end,
    country,
    river_basin,
    latitude,
    longitude,
    ST_AsGeoJSON(geometry)::json AS geometry_geojson,
    deaths,
    displaced,
    affected,
    severity,
    flood_impact_index,
    glide_number,
    url,
    h3_index,
    loaded_at
FROM {{ source('staging', 'flood_events') }}
