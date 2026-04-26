"""
Ingest the Copernicus Emergency Management Service (EMS) Rapid Mapping
activations into raw.copernicus_ems_events.

The full machine-readable feed of activations is at:
    https://emergency.copernicus.eu/mapping/list-of-activations-rapid

There is no fully-public stable JSON endpoint; some operators scrape the page
or consume the per-activation product packages. This module attempts to fetch
a public CSV/JSON listing if the operator has set ``COPERNICUS_EMS_FEED_URL``
(e.g. an institutional proxy) and falls back to the seed ``activations.csv``
that ships with the repo.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..db import insert_raw_records, log_ingestion, truncate_raw_table
from .common import (
    DownloadResult,
    download_to,
    new_batch_id,
    parse_with_fallback,
    raw_subdir,
)

logger = logging.getLogger(__name__)

SOURCE = "Copernicus_EMS"
DEFAULT_URL = (
    "https://emergency.copernicus.eu/mapping/list-of-activations-rapid?download=csv"
)
RAW_TABLE = "copernicus_ems_events"
SEED_FILE_NAME = "activations.csv"


def _read_dataframe(path: Path) -> pd.DataFrame:
    """Read activations.csv; auto-detects delimiter (CSV ships with ';')."""
    try:
        return pd.read_csv(path, sep=None, engine="python")
    except UnicodeDecodeError:
        return pd.read_csv(path, sep=None, engine="python", encoding="latin-1")


def run(*, full_refresh: bool = True) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("copernicus_ems")
    target_dir = raw_subdir("copernicus_ems")
    seed_path = target_dir / SEED_FILE_NAME
    target_file = target_dir / "activations_latest.csv"
    feed_url = os.getenv("COPERNICUS_EMS_FEED_URL", DEFAULT_URL)

    download: DownloadResult | None = None
    file_path: Path
    try:
        download = download_to(
            source=SOURCE,
            url=feed_url,
            target=target_file,
            fallback=seed_path if seed_path.exists() else None,
        )
        file_path = download.file_path
    except Exception as exc:  # noqa: BLE001
        if seed_path.exists():
            logger.warning("Copernicus EMS download failed (%s); using seed CSV", exc)
            file_path = seed_path
        else:
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="failure",
                source_url=feed_url,
                message=f"download error and no seed available: {exc}",
                started_at=started_at,
            )
            raise

    df, parser_fallback, parser_msg = parse_with_fallback(
        _read_dataframe,
        file_path,
        seed_path if seed_path.exists() and seed_path != file_path else None,
        source=SOURCE,
    )
    if parser_fallback:
        file_path = seed_path
    # Keep only flood-related activations to match the project's domain.
    if "Event Type" in df.columns:
        df = df[df["Event Type"].astype(str).str.contains("Flood", case=False, na=False)]
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if full_refresh:
        truncate_raw_table(RAW_TABLE)
    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=feed_url,
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=feed_url,
        file_path=str(file_path),
        file_checksum=download.checksum if download else None,
        message=(
            "parser fallback to seed: " + (parser_msg or "")
            if parser_fallback
            else ("download seed fallback used" if (download and download.used_fallback) else None)
        ),
        started_at=started_at,
    )
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    n = run()
    logger.info("Copernicus EMS ingestion complete (%s rows).", n)
