"""
Build / refresh the ``marts`` schema on top of ``staging.flood_events``.

The marts layer exposes a few denormalized, query-friendly tables/views
optimized for the FastAPI endpoints:

* ``marts.flood_events``                — flat, API-friendly projection
* ``marts.flood_events_by_region``      — country / region rollup
* ``marts.flood_events_by_h3``          — counts per H3 cell
* ``marts.flood_frequency_by_basin``    — yearly frequency by river basin
                                          (basin column is best-effort and
                                          may be NULL for sources that don't
                                          provide it; documented in README)

All objects are created as VIEWS or MATERIALIZED VIEWS so they refresh from
the canonical staging table without copying data.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from .db import get_connection

logger = logging.getLogger(__name__)


_DDL_STATEMENTS: list[str] = [
    # Ensure schema exists (defensive — schema.sql also creates it)
    "CREATE SCHEMA IF NOT EXISTS marts;",
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
    # NOTE: "basin" is approximated by country here — the unified schema in
    # schema.sql does not have a dedicated basin column. EM-DAT does provide
    # a River Basin field which is preserved in the raw JSONB payload and
    # could be promoted to a real column later (documented in README).
    """
    CREATE OR REPLACE VIEW marts.flood_frequency_by_basin AS
    SELECT
        COALESCE(country, 'Unknown')          AS basin,
        EXTRACT(YEAR FROM date_start)::INT    AS year,
        COUNT(*)                              AS event_count,
        AVG(severity)                         AS avg_severity
    FROM staging.flood_events
    GROUP BY basin, year
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
