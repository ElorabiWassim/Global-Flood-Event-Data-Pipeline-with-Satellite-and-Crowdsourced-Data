"""
Build / refresh the ``marts`` schema on top of ``staging.flood_events``.

The marts layer exposes a few denormalized, query-friendly tables/views
optimized for the FastAPI endpoints:

* ``marts.flood_events``                — flat, raw projection over staging
                                          (preserves both DFO vintages for
                                          auditability)
* ``marts.flood_events_unique``         — deduplicated event-level view that
                                          collapses Dartmouth_MasterList /
                                          Dartmouth_FO Register#-collisions.
                                          Prefer this view for analytics
                                          and dashboards.
* ``marts.flood_events_by_region``      — country / region rollup (deduped)
* ``marts.flood_events_by_h3``          — counts per H3 cell (deduped)
* ``marts.flood_events_by_month``       — monthly time-series (deduped)
* ``marts.flood_frequency_by_basin``    — yearly frequency by river basin
                                          (deduped). Uses the real
                                          ``river_basin`` column (populated
                                          mainly by EM-DAT) and falls back
                                          to country when the source doesn't
                                          provide a basin.

All objects are created as VIEWS so they refresh from the canonical staging
table without copying data.

Why dedupe? The ``Dartmouth_MasterList`` source (live MasterListrev.xlsx)
and ``Dartmouth_FO`` (HDX-frozen 2019 vintage) are both Brakenridge / DFO
"Global Active Archive of Large Floods" data with overlapping Register
numbers. Without dedup, ~73% of MasterList rows would double-count
Dartmouth_FO rows and inflate every aggregate impact statistic.
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
    "DROP VIEW IF EXISTS marts.flood_events_by_region   CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_by_h3       CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_frequency_by_basin CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_by_month    CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events_unique      CASCADE;",
    "DROP VIEW IF EXISTS marts.flood_events             CASCADE;",
    # ---- Flat raw projection over staging (preserves both DFO vintages) --
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
    # ---- Deduplicated event view (canonical for analytics) ----------------
    # Collapses two kinds of duplication:
    #   1. Cross-source: Dartmouth_MasterList vs Dartmouth_FO sharing the
    #      same DFO Register# (~73% overlap). Prefer Dartmouth_MasterList
    #      (live, more recent vintage).
    #   2. Intra-source: Dartmouth_FO carrying the same Register# both as
    #      ``2507`` and ``DFO_2507`` (residue of seed-CSV vs HDX-SHP runs).
    #      Prefer the bare Register# form.
    # Non-DFO sources (EM-DAT, Copernicus EMS, ReliefWeb) get a unique
    # partition key so every row is preserved.
    """
    CREATE OR REPLACE VIEW marts.flood_events_unique AS
    WITH normalized AS (
        SELECT
            *,
            CASE
                WHEN source IN ('Dartmouth_FO', 'Dartmouth_MasterList')
                  THEN regexp_replace(COALESCE(source_event_id, ''), '^DFO_', '')
                ELSE NULL
            END AS dfo_register_no
        FROM staging.flood_events
    ),
    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY
                    CASE
                        -- DFO-family rows with the same Register# share a partition
                        WHEN dfo_register_no IS NOT NULL AND dfo_register_no <> ''
                            THEN 'DFO:' || dfo_register_no
                        -- Everything else is its own partition (no dedup applied)
                        ELSE 'X:' || id::text
                    END
                ORDER BY
                    -- 1. Prefer the live MasterList over the frozen HDX vintage
                    CASE source
                        WHEN 'Dartmouth_MasterList' THEN 1
                        WHEN 'Dartmouth_FO'         THEN 2
                        ELSE 99
                    END,
                    -- 2. Within a source, prefer bare Register# over DFO_-prefixed
                    CASE WHEN source_event_id ~ '^DFO_' THEN 2 ELSE 1 END,
                    -- 3. Stable tiebreaker
                    id
            ) AS rn
        FROM normalized
    )
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
    FROM ranked
    WHERE rn = 1;
    """,
    # ---- Region rollup (over deduped events) ------------------------------
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
    FROM marts.flood_events_unique
    WHERE country IS NOT NULL
    GROUP BY country;
    """,
    # ---- H3 rollup (over deduped events) ----------------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events_by_h3 AS
    SELECT
        h3_index,
        COUNT(*)                    AS event_count,
        AVG(severity)               AS avg_severity,
        SUM(COALESCE(deaths, 0))    AS total_deaths,
        MIN(date_start)             AS earliest_event,
        MAX(date_start)             AS latest_event
    FROM marts.flood_events_unique
    WHERE h3_index IS NOT NULL
    GROUP BY h3_index;
    """,
    # ---- Yearly frequency by basin (over deduped events) -----------------
    # Uses the real river_basin column where available (mainly EM-DAT). For
    # rows without a basin we fall back to country so the mart still has
    # full coverage. The ``basin_source`` column tells consumers which kind
    # of grouping was used.
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
    FROM marts.flood_events_unique
    GROUP BY basin, basin_source, year
    ORDER BY basin, year;
    """,
    # ---- Time-series view (over deduped events) --------------------------
    """
    CREATE OR REPLACE VIEW marts.flood_events_by_month AS
    SELECT
        DATE_TRUNC('month', date_start)::DATE AS month,
        COUNT(*)                              AS event_count,
        AVG(severity)                         AS avg_severity,
        SUM(COALESCE(deaths, 0))              AS total_deaths
    FROM marts.flood_events_unique
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
