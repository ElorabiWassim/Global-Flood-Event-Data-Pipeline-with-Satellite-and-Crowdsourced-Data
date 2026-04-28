"""
Build / refresh the ``marts`` schema on top of ``staging.flood_events``.

The marts layer exposes a few denormalized, query-friendly tables/views
optimized for the FastAPI endpoints:

* ``marts.flood_events``                — flat, API-friendly projection
* ``marts.flood_events_by_region``      — country / region rollup
* ``marts.flood_events_by_h3``          — counts per H3 cell
* ``marts.flood_frequency_by_basin``    — yearly frequency by river basin.
                                          Uses the real ``river_basin`` column
                                          (populated mainly by EM-DAT) and
                                          falls back to country when the
                                          source doesn't provide a basin.

All objects are created as VIEWS or MATERIALIZED VIEWS so they refresh from
the canonical staging table without copying data.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from db.client import get_connection

logger = logging.getLogger(__name__)


_DDL_STATEMENTS: list[str] = [
    # Ensure schema exists (defensive — schema.sql also creates it)
    "CREATE SCHEMA IF NOT EXISTS marts;",
    # Drop existing views first so column-order / type changes work cleanly.
    # CREATE OR REPLACE VIEW only succeeds when the column list is identical.
    "DROP VIEW IF EXISTS marts.flood_events            CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_by_region  CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_by_h3      CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_frequency_by_basin CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_by_month   CASCADE;",
    # ---- Flat API-friendly view ------------------------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events AS
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
    FROM staging.flood_events;
    """,
    # ---- Region rollup ----------------------------------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events_by_region AS
    SELECT
        country,
        COUNT(*)                              AS event_count,
        SUM(COALESCE(deaths, 0))              AS total_deaths,
        SUM(COALESCE(displaced, 0))           AS total_displaced,
        SUM(COALESCE(affected, 0))            AS total_affected,
        AVG(severity)                         AS avg_severity,
        MIN(date_start)                       AS earliest_event,
        MAX(date_start)                       AS latest_event
    FROM staging.flood_events
    WHERE country IS NOT NULL
    GROUP BY country;
    """,
    # ---- H3 rollup --------------------------------------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events_by_h3 AS
    SELECT
        h3_index,
        COUNT(*)              AS event_count,
        AVG(severity)         AS avg_severity,
        SUM(COALESCE(deaths, 0))    AS total_deaths,
        MIN(date_start)       AS earliest_event,
        MAX(date_start)       AS latest_event
    FROM staging.flood_events
    WHERE h3_index IS NOT NULL
    GROUP BY h3_index;
    """,
    # ---- Yearly frequency by basin ---------------------------------------
    # Uses the real river_basin column where available (mainly EM-DAT). For
    # rows without a basin (Dartmouth, GloFAS CSV, Copernicus EMS, ReliefWeb)
    # we fall back to country so the mart still has full coverage. The
    # ``basin_source`` column tells consumers which kind of grouping was used.
    """
    CREATE OR REPLACE VIEW marts.flood_frequency_by_basin AS
    SELECT
        COALESCE(NULLIF(TRIM(river_basin), ''), country, 'Unknown')
                                              AS basin,
        CASE
            WHEN NULLIF(TRIM(river_basin), '') IS NOT NULL THEN 'river_basin'
            WHEN country IS NOT NULL                       THEN 'country_proxy'
            ELSE 'unknown'
        END                                   AS basin_source,
        EXTRACT(YEAR FROM date_start)::INT    AS year,
        COUNT(*)                              AS event_count,
        AVG(severity)                         AS avg_severity
    FROM staging.flood_events
    GROUP BY basin, basin_source, year
    ORDER BY basin, year;
    """,
    # ---- Time-series convenience view ------------------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events_by_month AS
    SELECT
        DATE_TRUNC('month', date_start)::DATE AS month,
        COUNT(*)                              AS event_count,
        AVG(severity)                         AS avg_severity,
        SUM(COALESCE(deaths, 0))              AS total_deaths
    FROM staging.flood_events
    GROUP BY 1
    ORDER BY 1;
    """,
]


def refresh_marts() -> None:
    """Apply every CREATE OR REPLACE statement (idempotent)."""
    with get_connection() as conn:
        for stmt in _DDL_STATEMENTS:
            cleaned = "\n".join(
                line for line in stmt.splitlines() if not line.strip().startswith("--")
            ).strip()
            if not cleaned:
                continue
            conn.execute(text(cleaned))
    logger.info("marts schema refreshed (%s statements)", len(_DDL_STATEMENTS))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    refresh_marts()
