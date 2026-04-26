"""
Ingest the Dartmouth Flood Observatory archive into raw.dartmouth_events.

Public source page:
    https://floodobservatory.colorado.edu/Archives/

The Observatory historically published an Excel master list (FloodArchive.xlsx
/ MasterListrev.xlsx) plus a per-year CSV. URLs are unstable, so this module
attempts the canonical XLSX and falls back to a seed CSV checked into
``data/raw/dartmouth/``.
"""

from __future__ import annotations

import logging
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

SOURCE = "Dartmouth_FO"
SOURCE_URL = "https://floodobservatory.colorado.edu/Archives/FloodArchive.xlsx"
RAW_TABLE = "dartmouth_events"
SEED_FILE_NAME = "dartmouth_floods.csv"


def _read_dataframe(path: Path) -> pd.DataFrame:
    """Read either xlsx or csv into a DataFrame."""
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    return pd.read_csv(path)


def run(*, full_refresh: bool = True) -> int:
    """Run the Dartmouth ingestion. Returns rows inserted."""
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("dartmouth")
    target_dir = raw_subdir("dartmouth")
    seed_path = target_dir / SEED_FILE_NAME
    target_xlsx = target_dir / "FloodArchive.xlsx"

    download: DownloadResult | None = None
    file_path: Path
    try:
        download = download_to(
            source=SOURCE,
            url=SOURCE_URL,
            target=target_xlsx,
            fallback=seed_path if seed_path.exists() else None,
        )
        file_path = download.file_path
    except Exception as exc:  # noqa: BLE001
        if seed_path.exists():
            logger.warning("Dartmouth download failed (%s); using seed CSV", exc)
            file_path = seed_path
        else:
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="failure",
                source_url=SOURCE_URL,
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
    df = df.where(pd.notnull(df), None)  # turn NaN into None for JSON
    records = df.to_dict(orient="records")

    if full_refresh:
        truncate_raw_table(RAW_TABLE)

    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=SOURCE_URL,
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=SOURCE_URL,
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
    logger.info("Dartmouth ingestion complete (%s rows).", n)
