"""
Ingest GloFAS / Global Active Archive of Large Flood Events.

Two related sources are covered here:

1. The Copernicus Global Flood Awareness System (GloFAS) reanalysis product is
   distributed via the Copernicus Climate Data Store (CDS). It REQUIRES a
   personal CDS API key (UID + API key in ``~/.cdsapirc``). When the key is
   present in the environment (``CDS_API_KEY`` / ``CDS_API_URL``), an
   integration point is exposed below for completing that download.

2. As an immediately-usable companion dataset, the Global Active Archive of
   Large Flood Events (Brakenridge / DFO) is publicly downloadable as the
   "Global Flood Records" CSV. We use that file as the canonical fallback
   so the pipeline always has data even when CDS credentials are missing.

In both cases, raw rows land in ``raw.glofas_events`` with their original
column names preserved as a JSONB payload.
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

SOURCE = "GloFAS"
# Public Global Active Archive of Large Floods
PUBLIC_URL = "https://floodobservatory.colorado.edu/temp/MasterListrev.xlsx"
RAW_TABLE = "glofas_events"
SEED_FILE_NAME = "global_flood_records.csv"


def _read_dataframe(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    # Some GFD-style CSVs use mojibake non-UTF8 headers; latin-1 is forgiving.
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")


def _has_cds_credentials() -> bool:
    return bool(os.getenv("CDS_API_KEY")) and bool(os.getenv("CDS_API_URL"))


def run(*, full_refresh: bool = True) -> int:
    """Run the GloFAS ingestion. Returns rows inserted."""
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("glofas")
    target_dir = raw_subdir("glofas")
    seed_path = target_dir / SEED_FILE_NAME
    target_file = target_dir / "global_flood_records.xlsx"

    if _has_cds_credentials():
        # Real CDS integration would go here: requires the cdsapi package and
        # downloads NetCDF reanalysis grids. We leave a clear placeholder
        # rather than fabricate data. Operators can extend this branch.
        logger.info(
            "[%s] CDS credentials detected — extend this function to call "
            "the cdsapi client. Falling back to public archive for now.",
            SOURCE,
        )

    download: DownloadResult | None = None
    file_path: Path
    try:
        download = download_to(
            source=SOURCE,
            url=PUBLIC_URL,
            target=target_file,
            fallback=seed_path if seed_path.exists() else None,
        )
        file_path = download.file_path
    except Exception as exc:  # noqa: BLE001
        if seed_path.exists():
            logger.warning("GloFAS download failed (%s); using seed CSV", exc)
            file_path = seed_path
        else:
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="failure",
                source_url=PUBLIC_URL,
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
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if full_refresh:
        truncate_raw_table(RAW_TABLE)
    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=PUBLIC_URL,
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=PUBLIC_URL,
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
    logger.info("GloFAS ingestion complete (%s rows).", n)
