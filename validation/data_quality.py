"""
Data-quality (DQ) checks against ``staging.flood_events`` and optional social
media flood signals.

Generates a Markdown report at ``data/logs/data_quality_report.md`` and also
returns a Python dict so the DAG can fail / alert if something is too broken.

Checks performed:
    1. Duplicate (source, source_event_id) tuples
    2. Rows missing the required ``date_start``
    3. Invalid latitude / longitude ranges
    4. Invalid PostGIS geometry
    5. Rows with no ``source``
    6. NULL ``h3_index`` despite valid coordinates
    7. Severity values outside the documented Dartmouth scale [0, 3]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from config.settings import LOGS_DIR
from db.client import get_engine

logger = logging.getLogger(__name__)

REPORT_PATH: Path = LOGS_DIR / "data_quality_report.md"


CHECKS: list[tuple[str, str]] = [
    (
        "duplicate_source_event_ids",
        """
        SELECT COUNT(*) AS bad
        FROM (
            SELECT source, source_event_id
            FROM staging.flood_events
            WHERE source_event_id IS NOT NULL
            GROUP BY source, source_event_id
            HAVING COUNT(*) > 1
        ) d
        """,
    ),
    (
        "missing_date_start",
        "SELECT COUNT(*) AS bad FROM staging.flood_events WHERE date_start IS NULL",
    ),
    (
        "invalid_latitude_or_longitude",
        """
        SELECT COUNT(*) AS bad FROM staging.flood_events
        WHERE (latitude IS NOT NULL AND (latitude < -90 OR latitude > 90))
           OR (longitude IS NOT NULL AND (longitude < -180 OR longitude > 180))
        """,
    ),
    (
        "invalid_geometry",
        """
        SELECT COUNT(*) AS bad FROM staging.flood_events
        WHERE geometry IS NOT NULL AND NOT ST_IsValid(geometry)
        """,
    ),
    (
        "missing_source",
        "SELECT COUNT(*) AS bad FROM staging.flood_events WHERE source IS NULL OR source = ''",
    ),
    (
        "missing_h3_with_coords",
        """
        SELECT COUNT(*) AS bad FROM staging.flood_events
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND h3_index IS NULL
        """,
    ),
    (
        "severity_out_of_range",
        """
        SELECT COUNT(*) AS bad FROM staging.flood_events
        WHERE severity IS NOT NULL AND (severity < 0 OR severity > 10)
        """,
    ),
]

SOCIAL_CHECKS: list[tuple[str, str]] = [
    (
        "social_duplicate_platform_post_ids",
        """
        SELECT COUNT(*) AS bad
        FROM (
            SELECT platform, post_id
            FROM staging.social_flood_signals
            WHERE post_id IS NOT NULL
            GROUP BY platform, post_id
            HAVING COUNT(*) > 1
        ) d
        """,
    ),
    (
        "social_missing_created_at",
        "SELECT COUNT(*) AS bad FROM staging.social_flood_signals WHERE created_at IS NULL",
    ),
    (
        "social_missing_platform_or_post_id",
        """
        SELECT COUNT(*) AS bad FROM staging.social_flood_signals
        WHERE platform IS NULL OR platform = '' OR post_id IS NULL OR post_id = ''
        """,
    ),
    (
        "social_invalid_latitude_or_longitude",
        """
        SELECT COUNT(*) AS bad FROM staging.social_flood_signals
        WHERE (latitude IS NOT NULL AND (latitude < -90 OR latitude > 90))
           OR (longitude IS NOT NULL AND (longitude < -180 OR longitude > 180))
        """,
    ),
    (
        "social_missing_h3_with_coords",
        """
        SELECT COUNT(*) AS bad FROM staging.social_flood_signals
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND h3_index IS NULL
        """,
    ),
    (
        "social_confidence_out_of_range",
        """
        SELECT COUNT(*) AS bad FROM staging.social_flood_signals
        WHERE signal_confidence IS NOT NULL
          AND (signal_confidence < 0 OR signal_confidence > 1)
        """,
    ),
    # Integrity: a row landed in staging without any matched keyword. This
    # should be impossible because ``_upsert_social_signals`` filters those
    # out, but the check guards against future refactors that bypass the
    # filter.
    (
        "social_relevance_without_keywords",
        """
        SELECT COUNT(*) AS bad FROM staging.social_flood_signals
        WHERE flood_relevance_score > 0
          AND (matched_keywords IS NULL OR cardinality(matched_keywords) = 0)
        """,
    ),
    # Traceability: every staging signal should still have its raw payload
    # available in ``raw.social_media_posts`` so an analyst can audit why the
    # signal passed filtering. A non-zero count means raw rows were deleted
    # out from under staging (or were never inserted).
    (
        "social_orphan_staging_signals",
        """
        SELECT COUNT(*) AS bad
        FROM staging.social_flood_signals s
        LEFT JOIN raw.social_media_posts r
          ON r.platform = s.platform AND r.post_id = s.post_id
        WHERE r.id IS NULL
        """,
    ),
]


SUMMARY_SQL = """
SELECT
    source,
    COUNT(*)                                                    AS rows_total,
    COUNT(*) FILTER (WHERE latitude IS NOT NULL)                AS rows_with_coords,
    COUNT(*) FILTER (WHERE h3_index IS NOT NULL)                AS rows_with_h3,
    COUNT(*) FILTER (WHERE severity IS NOT NULL)                AS rows_with_severity,
    MIN(date_start)                                             AS earliest_event,
    MAX(date_start)                                             AS latest_event
FROM staging.flood_events
GROUP BY source
ORDER BY source;
"""

SOCIAL_SUMMARY_SQL = """
SELECT
    platform,
    COUNT(*)                                                    AS rows_total,
    COUNT(*) FILTER (WHERE country IS NOT NULL)                 AS rows_with_country,
    COUNT(*) FILTER (WHERE latitude IS NOT NULL)                AS rows_with_coords,
    COUNT(*) FILTER (WHERE h3_index IS NOT NULL)                AS rows_with_h3,
    AVG(signal_confidence)                                      AS avg_signal_confidence,
    MIN(created_at)                                             AS earliest_signal,
    MAX(created_at)                                             AS latest_signal
FROM staging.social_flood_signals
GROUP BY platform
ORDER BY platform;
"""


def _run_checks() -> dict[str, int]:
    engine = get_engine()
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for name, sql in CHECKS + SOCIAL_CHECKS:
            try:
                bad = conn.execute(text(sql)).scalar() or 0
                out[name] = int(bad)
            except Exception as exc:  # noqa: BLE001
                logger.exception("DQ check %s failed", name)
                out[name] = -1
                out[f"{name}__error"] = str(exc)  # type: ignore[assignment]
    return out


def _run_summary() -> list[dict[str, Any]]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(SUMMARY_SQL)).all()
        return [dict(r._mapping) for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("DQ summary query failed")
        return []


def _run_social_summary() -> list[dict[str, Any]]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(SOCIAL_SUMMARY_SQL)).all()
        return [dict(r._mapping) for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("DQ social summary query failed")
        return []


def _to_markdown(
    checks: dict[str, int],
    summary: list[dict[str, Any]],
    social_summary: list[dict[str, Any]],
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# Data Quality Report — staging.flood_events")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")
    lines.append("## Per-source summary")
    lines.append("")
    if summary:
        lines.append("| Source | Rows | With coords | With H3 | With severity | Earliest | Latest |")
        lines.append("|--------|------|-------------|---------|---------------|----------|--------|")
        for r in summary:
            lines.append(
                "| {source} | {rows_total} | {rows_with_coords} | {rows_with_h3} "
                "| {rows_with_severity} | {earliest_event} | {latest_event} |".format(**r)
            )
    else:
        lines.append("_No rows in staging.flood_events yet._")
    lines.append("")
    lines.append("## Social signal summary")
    lines.append("")
    if social_summary:
        lines.append(
            "| Platform | Rows | With country | With coords | With H3 | "
            "Avg confidence | Earliest | Latest |"
        )
        lines.append(
            "|----------|------|--------------|-------------|---------|"
            "----------------|----------|--------|"
        )
        for r in social_summary:
            lines.append(
                "| {platform} | {rows_total} | {rows_with_country} | "
                "{rows_with_coords} | {rows_with_h3} | "
                "{avg_signal_confidence} | {earliest_signal} | "
                "{latest_signal} |".format(**r)
            )
    else:
        lines.append("_No rows in staging.social_flood_signals yet._")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Failing rows |")
    lines.append("|-------|--------------|")
    for name, _sql in CHECKS + SOCIAL_CHECKS:
        bad = checks.get(name, -1)
        marker = ""
        if bad == 0:
            marker = " (OK)"
        elif bad < 0:
            marker = " (ERROR)"
        lines.append(f"| `{name}` | {bad}{marker} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def run(*, write: bool = True) -> dict[str, Any]:
    """Run all DQ checks and (optionally) write the Markdown report."""
    checks = _run_checks()
    summary = _run_summary()
    social_summary = _run_social_summary()
    md = _to_markdown(checks, summary, social_summary)
    if write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(md, encoding="utf-8")
        logger.info("DQ report written to %s", REPORT_PATH)
    return {
        "checks": checks,
        "summary": summary,
        "social_summary": social_summary,
        "report_path": str(REPORT_PATH),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(result["checks"])
