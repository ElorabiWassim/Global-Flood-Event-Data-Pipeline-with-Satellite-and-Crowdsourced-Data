-- Optional dbt staging model for the Dartmouth Flood Observatory raw payloads.
-- The Python pipeline (transformations/transform.py) is the canonical loader;
-- this model is provided so analysts can also consume raw data through dbt.
{{ config(materialized='view', schema='staging') }}

WITH src AS (
    SELECT payload
    FROM {{ source('raw', 'dartmouth_events') }}
)
SELECT
    'Dartmouth_FO'                                       AS source,
    payload->>'event_id'                                 AS source_event_id,
    NULLIF(payload->>'main_cause', '')                   AS main_cause,
    NULLIF(payload->>'country',    '')                   AS country,
    NULLIF(payload->>'date_start', '')::TIMESTAMP        AS date_start,
    NULLIF(payload->>'date_end',   '')::TIMESTAMP        AS date_end,
    NULLIF(payload->>'latitude',   '')::DOUBLE PRECISION AS latitude,
    NULLIF(payload->>'longitude',  '')::DOUBLE PRECISION AS longitude,
    NULLIF(payload->>'deaths',     '')::INTEGER          AS deaths,
    NULLIF(payload->>'displaced',  '')::INTEGER          AS displaced,
    NULLIF(payload->>'severity',   '')::DOUBLE PRECISION AS severity,
    NULLIF(payload->>'glide_number', '')                 AS glide_number,
    NULLIF(payload->>'source_url', '')                   AS url
FROM src
