"""Diagnostic for the displaced totals across DFO-family sources.

This script was originally written to investigate why total displaced was
~1.6B and confirmed that:
  H1  Dartmouth_MasterList (formerly mislabelled 'GloFAS') and Dartmouth_FO
      are different vintages of the same DFO archive  ->  double counting.
  H2  A handful of mega-events (1998 Bangladesh, 2004 India, ...) dominate.
  H3  DFO's `Displaced` is broader than 'permanently displaced'.
  H4  Raw values are not inflated by our parser.

It is now used as a regression check after the marts.flood_events_unique
view was added to dedupe DFO-family Register# collisions.
"""
from __future__ import annotations

from sqlalchemy import text

from db.client import get_engine

eng = get_engine()


def show(title: str, sql: str, params: dict | None = None) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    with eng.connect() as c:
        rows = c.execute(text(sql), params or {}).all()
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0]._mapping.keys())
    widths = [max(len(c), max(len(str(r._mapping[c])) for r in rows)) for c in cols]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(r._mapping[c]).ljust(w) for c, w in zip(cols, widths)))


# ----------------------------------------------------------------------
# 1) Per-source reality check
# ----------------------------------------------------------------------
show(
    "Per-source totals + how many events have a non-null displaced value",
    """
    SELECT source,
           COUNT(*)                              AS n_events,
           COUNT(displaced)                      AS n_with_displaced,
           COALESCE(SUM(displaced), 0)::bigint   AS sum_displaced,
           COALESCE(MAX(displaced), 0)::bigint   AS max_displaced,
           ROUND(AVG(displaced)::numeric, 0)     AS avg_displaced
    FROM marts.flood_events
    GROUP BY source
    ORDER BY sum_displaced DESC
    """,
)

# ----------------------------------------------------------------------
# 2) Top 10 highest-displaced single events  (to find unrealistic values)
# ----------------------------------------------------------------------
show(
    "Top 10 single events by displaced  (across all sources)",
    """
    SELECT source, source_event_id, country, date_start::date AS started,
           displaced, deaths
    FROM marts.flood_events
    WHERE displaced IS NOT NULL
    ORDER BY displaced DESC
    LIMIT 10
    """,
)

# ----------------------------------------------------------------------
# 3) Long tail: how concentrated is the total in the top events?
# ----------------------------------------------------------------------
show(
    "Concentration: top-N events as % of total displaced",
    """
    WITH ranked AS (
        SELECT displaced,
               SUM(displaced) OVER ()                                  AS grand_total,
               ROW_NUMBER() OVER (ORDER BY displaced DESC NULLS LAST)  AS rk
        FROM marts.flood_events
        WHERE displaced IS NOT NULL
    )
    SELECT 'top 10'    AS bucket,
           SUM(displaced)::bigint                                     AS sum_in_bucket,
           ROUND(100.0 * SUM(displaced) / MAX(grand_total), 2)         AS pct_of_total
    FROM ranked WHERE rk <= 10
    UNION ALL
    SELECT 'top 100',
           SUM(displaced)::bigint,
           ROUND(100.0 * SUM(displaced) / MAX(grand_total), 2)
    FROM ranked WHERE rk <= 100
    UNION ALL
    SELECT 'top 1000',
           SUM(displaced)::bigint,
           ROUND(100.0 * SUM(displaced) / MAX(grand_total), 2)
    FROM ranked WHERE rk <= 1000
    """,
)

# ----------------------------------------------------------------------
# 4) Hypothesis H1: are GloFAS and Dartmouth_FO duplicates?
#    Compare them head-to-head on (date_start, country) overlap.
# ----------------------------------------------------------------------
show(
    "Overlap between Dartmouth_MasterList and Dartmouth_FO on (date_start, country)",
    """
    WITH d AS (
        SELECT date_start::date AS d, country FROM marts.flood_events
        WHERE source = 'Dartmouth_FO' AND date_start IS NOT NULL AND country IS NOT NULL
    ),
    g AS (
        SELECT date_start::date AS d, country FROM marts.flood_events
        WHERE source = 'Dartmouth_MasterList' AND date_start IS NOT NULL AND country IS NOT NULL
    )
    SELECT
        (SELECT COUNT(*) FROM d)                                                AS dartmouth_with_dc,
        (SELECT COUNT(*) FROM g)                                                AS masterlist_with_dc,
        (SELECT COUNT(*) FROM d INNER JOIN g USING (d, country))                AS exact_overlap_rows,
        (SELECT COUNT(DISTINCT (d, country)) FROM d INNER JOIN g USING (d, country)) AS overlap_distinct_keys
    """,
)

# ----------------------------------------------------------------------
# 5) Are source_event_id ranges identical?  DFO Register numbers should be
#    unique to the DFO archive — if both sources share them, they are dupes.
# ----------------------------------------------------------------------
show(
    "source_event_id ranges per source (DFO Register numbers should match if duplicate)",
    """
    SELECT source,
           MIN(source_event_id) AS first_id,
           MAX(source_event_id) AS last_id,
           COUNT(DISTINCT source_event_id) AS distinct_ids
    FROM marts.flood_events
    WHERE source IN ('Dartmouth_MasterList','Dartmouth_FO')
    GROUP BY source
    """,
)

# ----------------------------------------------------------------------
# 6) Same Register number in both sources?
# ----------------------------------------------------------------------
show(
    "Events present in BOTH Dartmouth_MasterList and Dartmouth_FO with same source_event_id",
    """
    SELECT g.source_event_id,
           g.country, g.date_start::date AS started,
           g.displaced AS masterlist_disp, d.displaced AS dartmouth_disp,
           g.deaths    AS masterlist_dead, d.deaths    AS dartmouth_dead
    FROM marts.flood_events g
    JOIN marts.flood_events d
      ON g.source_event_id = d.source_event_id
     AND g.source = 'Dartmouth_MasterList' AND d.source = 'Dartmouth_FO'
    ORDER BY g.displaced DESC NULLS LAST
    LIMIT 10
    """,
)

# ----------------------------------------------------------------------
# 7) Cross-check raw payload — is `Displaced` value already huge upstream,
#    or did our parser inflate it?
# ----------------------------------------------------------------------
show(
    "Raw payload sample for the largest Dartmouth_MasterList displaced events",
    """
    SELECT (payload ->> 'ID')        AS dfo_id,
           (payload ->> 'Country')   AS country,
           (payload ->> 'Began')     AS began,
           (payload ->> 'Displaced') AS raw_displaced,
           (payload ->> 'Dead')      AS raw_dead
    FROM raw.glofas_events
    WHERE (payload ->> 'Displaced') ~ '^[0-9.]+$'
    ORDER BY (payload ->> 'Displaced')::numeric DESC NULLS LAST
    LIMIT 10
    """,
)

# ----------------------------------------------------------------------
# 8) IMPACT: regression check now that marts.flood_events_unique is the
#    canonical analytical view. The view should drop ~4.6k DFO-family
#    duplicates and ~850M displaced double-counts.
# ----------------------------------------------------------------------
show(
    "Headline KPIs: raw projection vs the deduped view",
    """
    SELECT
        (SELECT COUNT(*)                            FROM marts.flood_events)        AS events_raw,
        (SELECT COUNT(*)                            FROM marts.flood_events_unique) AS events_deduped,
        (SELECT COALESCE(SUM(deaths),    0)::bigint FROM marts.flood_events)        AS deaths_raw,
        (SELECT COALESCE(SUM(deaths),    0)::bigint FROM marts.flood_events_unique) AS deaths_deduped,
        (SELECT COALESCE(SUM(displaced), 0)::bigint FROM marts.flood_events)        AS displaced_raw,
        (SELECT COALESCE(SUM(displaced), 0)::bigint FROM marts.flood_events_unique) AS displaced_deduped
    """,
)
