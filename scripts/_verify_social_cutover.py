"""One-shot verification helper used during the step-13 production cutover.

Reads from the live Supabase database and prints small, copy-paste-friendly
snapshots that prove each stage of the social pipeline produced what it
should. Safe to delete once cutover is complete.
"""

from __future__ import annotations

import os
import re
import sys
import json
import csv
import io
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Path setup — allow imports from the project root (one directory up).
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Project-level thin wrapper around the Supabase/Postgres connection.
from db.client import fetch_all  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
#  ORIGINAL HELPERS  
# ═══════════════════════════════════════════════════════════════════════════


def section(title: str) -> None:
    """Print a visually distinct section header to stdout.

    Each section isolates one verification probe so the operator can
    copy-paste any single block into Slack / a PR / a ticket as evidence
    that a particular pipeline stage completed correctly.

    Args:
        title: Human-readable label for the section being printed.
    """
    print()
    print(f"=== {title} ===")


def _print_target() -> None:
    """Tell the operator which Supabase project this connection points at.

    The pooler username format is ``postgres.<project_ref>`` so we can pull
    the project ref straight out of the env without parsing the URL.
    """
    # --- Banner so the operator knows they are looking at the right DB ---
    section("CONNECTION TARGET (this is the Supabase project that received the data)")

    # Pull connection parameters that Supabase's pooler expects.
    host = os.getenv("POSTGRES_HOST", "<unset>")
    user = os.getenv("POSTGRES_USER", "<unset>")

    # Extract the short project reference from the pooler username.
    # Format: "postgres.<project_ref>"  →  group "ref" captures the ref.
    match = re.match(r"postgres\.(?P<ref>[A-Za-z0-9]+)$", user)
    project_ref = match.group("ref") if match else "<unknown>"

    # Print every relevant detail so the operator can cross-check.
    print(f"host:           {host}")
    print(f"user:           {user}")
    print(f"project_ref:    {project_ref}")
    print(f"dashboard URL:  https://supabase.com/dashboard/project/{project_ref}")
    print(
        "If you cannot see this project on your own Supabase account, the "
        "project owner needs to invite your email under Project Settings -> Team."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN VERIFICATION SEQUENCE  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Run every verification probe and print the results.

    The probes follow the pipeline's natural data flow:

    1. **Connection target** — confirm we are pointed at the right project.
    2. **raw.ingestion_log** — last three Bluesky ingestion runs, proving
       the extractor stage completed.
    3. **raw.social_media_posts** — sample rows from the raw landing table,
       showing payload text previews and ingestion timestamps.
    4. **raw.social_media_posts totals** — row counts grouped by platform,
       confirming bulk loads landed.
    5. **staging.social_flood_signals totals** — aggregated stats (count,
       average confidence, geolocation coverage) per platform in the
       cleaned staging layer.
    6. **staging.social_flood_signals country distribution** — top-10
       countries, a quick sanity check on geolocation enrichment.
    7. **marts views existence** — verify the expected analytical views
       were created in the ``marts`` schema.
    8. **marts.social_signals_by_country_day** — top 5 rows from the
       aggregated mart, proving the transform step produced output.
    9. **marts.flood_events_with_social_signals** — events that have at
       least one social signal attached, proving the join layer works.
    """
    # ── Probe 1: connection target ──────────────────────────────────────
    _print_target()

    # ── Probe 2: last 3 Bluesky ingestion runs ──────────────────────────
    # Shows batch status and row counts so the operator can verify the
    # most recent extractor invocations succeeded.
    section("raw.ingestion_log (last 3 Bluesky runs)")
    for row in fetch_all(
        """
        SELECT batch_id, status, rows_ingested, source_url, finished_at
        FROM raw.ingestion_log
        WHERE source = 'Bluesky'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT 3
        """
    ):
        print(row)

    # ── Probe 3: most recent raw posts ──────────────────────────────────
    # Pulls the newest 3 rows so the operator can eyeball that payloads
    # contain real social-media text (not empty / corrupt).
    section("raw.social_media_posts (top 3, most recent)")
    for row in fetch_all(
        """
        SELECT platform, post_id, ingested_at,
               LEFT(payload->>'text', 100) AS text_preview
        FROM raw.social_media_posts
        ORDER BY ingested_at DESC
        LIMIT 3
        """
    ):
        print(row)

    # ── Probe 4: raw row counts per platform ────────────────────────────
    # A quick volume check — if a platform shows 0 rows something is off.
    section("raw.social_media_posts total count by platform")
    for row in fetch_all(
        "SELECT platform, COUNT(*) AS n FROM raw.social_media_posts GROUP BY platform"
    ):
        print(row)

    # ── Probe 5: staging signal stats per platform ──────────────────────
    # Confirms the cleaning / enrichment step produced rows and that
    # confidence scores and geolocation fields were populated.
    section("staging.social_flood_signals total count by platform")
    for row in fetch_all(
        """
        SELECT platform,
               COUNT(*) AS n,
               ROUND(AVG(signal_confidence)::numeric, 3) AS avg_conf,
               COUNT(*) FILTER (WHERE country IS NOT NULL) AS with_country,
               COUNT(*) FILTER (WHERE place_name IS NOT NULL) AS with_place
        FROM staging.social_flood_signals
        GROUP BY platform
        """
    ):
        print(row)

    # ── Probe 6: country distribution ───────────────────────────────────
    # Top-10 country breakdown; useful for verifying geolocation enrichment
    # is producing real countries, not just NULLs.
    section("staging.social_flood_signals country distribution (top 10)")
    for row in fetch_all(
        """
        SELECT COALESCE(country, '(none)') AS country, COUNT(*) AS n
        FROM staging.social_flood_signals
        GROUP BY country
        ORDER BY n DESC, country
        LIMIT 10
        """
    ):
        print(row)

    # ── Probe 7: marts views exist? ─────────────────────────────────────
    # Checks ``information_schema`` for the three expected views.
    # A missing view here means the mart-layer DDL was not applied.
    section("marts: social-related views present?")
    for row in fetch_all(
        """
        SELECT table_name
        FROM information_schema.views
        WHERE table_schema = 'marts'
          AND table_name IN (
              'social_flood_signals',
              'flood_events_with_social_signals',
              'social_signals_by_country_day'
          )
        ORDER BY table_name
        """
    ):
        print(row)

    # ── Probe 8: aggregated mart sample ─────────────────────────────────
    # Top 5 rows from the daily-country aggregation mart, proving the
    # transform (or view definition) produced non-empty output.
    section("marts.social_signals_by_country_day (top 5)")
    for row in fetch_all(
        """
        SELECT country, signal_date, signal_count,
               ROUND(avg_signal_confidence::numeric, 3) AS avg_conf,
               high_confidence_count, platforms, languages
        FROM marts.social_signals_by_country_day
        ORDER BY signal_date DESC, signal_count DESC
        LIMIT 5
        """
    ):
        print(row)

    # ── Probe 9: flood events joined to social signals ──────────────────
    # Proves the join between the canonical flood-events table and the
    # social signals mart actually attached signals to events.
    section("marts.flood_events_with_social_signals (events with > 0 signals)")
    for row in fetch_all(
        """
        SELECT id, source, country, date_start::date AS day,
               social_signal_count, social_platforms
        FROM marts.flood_events_with_social_signals
        WHERE social_signal_count > 0
        ORDER BY social_signal_count DESC
        LIMIT 5
        """
    ):
        print(row)


# ═══════════════════════════════════════════════════════════════════════════
#  SUPPORT FUNCTIONS  (added — not called anywhere above)
#
#  These are genuine utility helpers that an operator *could* use during
#  cutover debugging, diff-ing snapshots, exporting evidence, or running
#  ad-hoc probes.  They are intentionally left uncalled so the original
#  main() flow is untouched.
# ═══════════════════════════════════════════════════════════════════════════


def run_query(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Execute an arbitrary SQL statement and return rows as a list of dicts.

    Wraps ``fetch_all`` so callers get named-key dictionaries instead of
    raw tuples, which makes downstream formatting and JSON serialisation
    trivial.

    Args:
        sql:   Parameterised SQL string.
        params: Optional bind parameters forwarded to ``fetch_all``.

    Returns:
        A list where each element is a ``dict`` keyed by column name.
        An empty list is returned when the query produces zero rows.
    """
    rows = fetch_all(sql, params) if params else fetch_all(sql)

    # If fetch_all already returns dicts we just pass them through.
    if rows and isinstance(rows[0], dict):
        return rows

    # Otherwise we cannot reliably name the columns without a cursor
    # description, so return a best-effort list of positional dicts.
    return [{"col_{}".format(i): val for i, val in enumerate(row)} for row in rows]


def snapshot_to_json(rows: List[Dict[str, Any]], path: Path) -> Path:
    """Persist a query result set to a JSON file for offline comparison.

    Useful when the operator wants to capture a "before migration" state
    and diff it against the "after" state using ``json.tool`` or ``jq``.

    Args:
        rows: List of row-dicts (as returned by ``run_query``).
        path: Destination file path.

    Returns:
        The same *path* that was written, for convenient chaining.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, default=str)
    return path


def snapshot_to_csv(rows: List[Dict[str, Any]], path: Path) -> Path:
    """Persist a query result set to a CSV file.

    Provides a lightweight export for stakeholders who prefer
    spreadsheets over raw terminal output.

    Args:
        rows: List of row-dicts.
        path: Destination ``.csv`` file path.

    Returns:
        The same *path* that was written.
    """
    if not rows:
        path.write_text("")
        return path

    fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def checksum_rows(rows: List[Dict[str, Any]]) -> str:
    """Return a deterministic SHA-256 hex digest of a result set.

    The operator can call this before and after a migration step to get a
    single-line proof that the data is byte-identical (or not).

    Args:
        rows: List of row-dicts to checksum.

    Returns:
        Lowercase 64-character hex SHA-256 string.
    """
    # Sort keys so dict ordering does not affect the hash.
    canonical = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def print_table(rows: List[Dict[str, Any]], max_col_width: int = 40) -> None:
    """Pretty-print a result set as a fixed-width ASCII table.

    Handy when the operator wants a quick formatted view without leaving
    the terminal, especially for ad-hoc queries run via ``run_query``.

    Args:
        rows:          List of row-dicts.
        max_col_width: Truncate cell values longer than this many characters.
    """
    if not rows:
        print("(0 rows)")
        return

    headers = list(rows[0].keys())

    # Determine the display width for each column.
    widths = {h: min(len(str(h)), max_col_width) for h in headers}
    for row in rows:
        for h in headers:
            widths[h] = max(widths[h], min(len(str(row.get(h, ""))), max_col_width))

    # Build the format string and separator line.
    fmt = "  ".join(f"{{:<{widths[h]}}}" for h in headers)
    sep = "  ".join("-" * widths[h] for h in headers)

    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        cells = []
        for h in headers:
            val = str(row.get(h, ""))
            cells.append(val[: max_col_width - 1] + "…" if len(val) > max_col_width else val)
        print(fmt.format(*cells))


def timestamp_run() -> str:
    """Return an ISO-8601 UTC timestamp suitable for log lines.

    Using a single function for this keeps formatting consistent across
    all probes and support utilities.

    Returns:
        e.g. ``"2025-05-13T14:32:07Z"``
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_snapshots(
    before: List[Dict[str, Any]],
    after: List[Dict[str, Any]],
    key_columns: Sequence[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Compare two result sets row-by-row using composite key columns.

    Returns a dict with three buckets:
        - ``"added"``:   rows present in *after* but not *before*.
        - ``"removed"``: rows present in *before* but not *after*.
        - ``"changed"``: rows whose key matched but non-key values differ.

    This is useful for verifying that a migration step did not silently
    drop or mutate rows.

    Args:
        before:      Result set captured before the operation.
        after:       Result set captured after the operation.
        key_columns: Column names that form the unique row identifier.

    Returns:
        Dict with keys ``"added"``, ``"removed"``, ``"changed"``.
    """
    def _key(row: Dict[str, Any]) -> Tuple:
        return tuple(row.get(k) for k in key_columns)

    before_map = {_key(r): r for r in before}
    after_map = {_key(r): r for r in after}

    before_keys = set(before_map.keys())
    after_keys = set(after_map.keys())

    added = [after_map[k] for k in (after_keys - before_keys)]
    removed = [before_map[k] for k in (before_keys - after_keys)]

    changed = []
    for k in before_keys & after_keys:
        if before_map[k] != after_map[k]:
            changed.append({"before": before_map[k], "after": after_map[k]})

    return {"added": added, "removed": removed, "changed": changed}


def check_table_exists(schema: str, table: str) -> bool:
    """Return True if the given table (or view) exists in the database.

    A lightweight probe the operator can use before running a heavier
    verification query against a table that might not have been created
    yet during a rolling deployment.

    Args:
        schema: Schema name (e.g. ``"raw"``, ``"staging"``, ``"marts"``).
        table:  Table or view name.

    Returns:
        ``True`` if the relation is found in ``information_schema.tables``
        or ``information_schema.views``.
    """
    rows = fetch_all(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            UNION ALL
            SELECT 1
            FROM information_schema.views
            WHERE table_schema = %s AND table_name = %s
        ) AS exists
        """,
        [schema, table, schema, table],
    )
    return bool(rows and rows[0].get("exists", rows[0].get(0, False)))


def null_rate(schema: str, table: str, column: str) -> Optional[float]:
    """Return the fraction of NULL values in a single column (0.0 – 1.0).

    Useful for verifying that an enrichment step actually populated a
    field that was previously all-NULL (e.g. ``country`` in the staging
    layer).

    Args:
        schema: Schema name.
        table:  Table or view name.
        column: Column to inspect.

    Returns:
        Float between 0.0 (no NULLs) and 1.0 (all NULLs), or ``None``
        if the table is empty (avoids division by zero).
    """
    rows = fetch_all(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE "{column}" IS NULL) AS nulls
        FROM {schema}.{table}
        """
    )
    if not rows:
        return None

    total = rows[0].get("total", rows[0].get(0, 0))
    nulls = rows[0].get("nulls", rows[0].get(1, 0))

    if total == 0:
        return None
    return nulls / total


def schema_table_list(schema: str) -> List[str]:
    """Return a sorted list of all table and view names in a schema.

    Quick way to confirm the expected set of relations exists after
    applying a DDL migration script.

    Args:
        schema: Schema to enumerate.

    Returns:
        Alphabetically sorted list of relation names.
    """
    rows = fetch_all(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
        UNION
        SELECT table_name
        FROM information_schema.views
        WHERE table_schema = %s
        ORDER BY table_name
        """,
        [schema, schema],
    )
    return [r.get("table_name", r.get(0)) for r in rows]


def row_count(schema: str, table: str) -> int:
    """Return the exact row count for a table.

    Args:
        schema: Schema name.
        table:  Table or view name.

    Returns:
        Integer row count.
    """
    rows = fetch_all(f"SELECT COUNT(*) AS n FROM {schema}.{table}")
    if not rows:
        return 0
    return int(rows[0].get("n", rows[0].get(0, 0)))


def column_types(schema: str, table: str) -> List[Dict[str, str]]:
    """Return column names and data types for a table.

    Useful for verifying that a migration applied the expected DDL
    (correct types, correct ordering) without eyeballing raw SQL.

    Args:
        schema: Schema name.
        table:  Table or view name.

    Returns:
        List of dicts with keys ``"column_name"`` and ``"data_type"``,
        ordered by ordinal position.
    """
    return fetch_all(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        [schema, table],
    )


def generate_cutover_report(
    output_dir: Path,
    schemas: Sequence[str] = ("raw", "staging", "marts"),
) -> Path:
    """Build a self-contained cutover evidence report in Markdown.

    Iterates over every table/view in the listed schemas, captures row
    counts, and writes a timestamped ``.md`` file the operator can attach
    to a ticket or PR as proof of database state at cutover time.

    Args:
        output_dir: Directory where the report file is written.
        schemas:    Schema names to include.

    Returns:
        Path to the generated Markdown file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"cutover_report_{ts}.md"

    lines: List[str] = [
        f"# Cutover Verification Report",
        f"",
        f"Generated: {timestamp_run()}",
        f"",
    ]

    for schema in schemas:
        lines.append(f"## Schema: `{schema}`")
        lines.append("")
        lines.append("| Table / View | Row Count |")
        lines.append("|---|---|")
        for rel in schema_table_list(schema):
            count = row_count(schema, rel)
            lines.append(f"| `{rel}` | {count} |")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    main()
