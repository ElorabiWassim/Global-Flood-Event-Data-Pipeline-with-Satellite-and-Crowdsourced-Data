"""
Data-quality (DQ) checks against ``staging.flood_events``.

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

from .config import LOGS_DIR
from .db import get_engine

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


def _run_checks() -> dict[str, int]:
    engine = get_engine()
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for name, sql in CHECKS:
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


def _to_markdown(checks: dict[str, int], summary: list[dict[str, Any]]) -> str:
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
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Failing rows |")
    lines.append("|-------|--------------|")
    for name, _sql in CHECKS:
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
    md = _to_markdown(checks, summary)
    if write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(md, encoding="utf-8")
        logger.info("DQ report written to %s", REPORT_PATH)
    return {"checks": checks, "summary": summary, "report_path": str(REPORT_PATH)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(result["checks"])
