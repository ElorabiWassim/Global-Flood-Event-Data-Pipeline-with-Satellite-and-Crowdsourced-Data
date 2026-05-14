"""One-shot verification helper used during the step-13 production cutover.

Reads from the live Supabase database and prints small, copy-paste-friendly
snapshots that prove each stage of the social pipeline produced what it
should. Safe to delete once cutover is complete.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.client import fetch_all  # noqa: E402


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def _print_target() -> None:
    """Tell the operator which Supabase project this connection points at.

    The pooler username format is ``postgres.<project_ref>`` so we can pull
    the project ref straight out of the env without parsing the URL.
    """
    section("CONNECTION TARGET (this is the Supabase project that received the data)")
    host = os.getenv("POSTGRES_HOST", "<unset>")
    user = os.getenv("POSTGRES_USER", "<unset>")
    match = re.match(r"postgres\.(?P<ref>[A-Za-z0-9]+)$", user)
    project_ref = match.group("ref") if match else "<unknown>"
    print(f"host:           {host}")
    print(f"user:           {user}")
    print(f"project_ref:    {project_ref}")
    print(f"dashboard URL:  https://supabase.com/dashboard/project/{project_ref}")
    print(
        "If you cannot see this project on your own Supabase account, the "
        "project owner needs to invite your email under Project Settings -> Team."
    )


def main() -> None:
    _print_target()
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

    section("raw.social_media_posts total count by platform")
    for row in fetch_all(
        "SELECT platform, COUNT(*) AS n FROM raw.social_media_posts GROUP BY platform"
    ):
        print(row)

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


if __name__ == "__main__":
    main()
