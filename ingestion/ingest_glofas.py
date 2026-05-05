"""
Ingest the Dartmouth Flood Observatory live MasterList.

Despite the historical filename ``glofas_events`` (kept for backwards
compatibility with the existing raw schema), this ingester does NOT pull
from the Copernicus Global Flood Awareness System. The public ``MasterList``
XLSX at ``floodobservatory.colorado.edu`` is the live vintage of the same
Dartmouth Flood Observatory "Global Active Archive of Large Floods" that
``ingest_dartmouth.py`` pulls from HDX (frozen 2019 vintage).

We call this source ``Dartmouth_MasterList`` to make the lineage explicit
and to enable downstream deduplication against ``Dartmouth_FO`` (see the
``marts.flood_events_unique`` view).

The Copernicus GloFAS reanalysis product is a separate dataset entirely
(NetCDF streamflow grids via the CDS API). If ``CDS_API_KEY`` /
``CDS_API_URL`` are present an integration point can be added here, but it
would feed a different raw table (e.g. ``raw.glofas_reanalysis``) and a
different normalizer.

Raw rows land in ``raw.glofas_events`` with their original column names
preserved as a JSONB payload.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from db.client import insert_raw_records, log_ingestion, truncate_raw_table
from .common import (
    DownloadResult,
    download_to,
    new_batch_id,
    parse_with_fallback,
    raw_subdir,
)

logger = logging.getLogger(__name__)

# Canonical source label written into staging.flood_events.source. The raw
# table name is kept as ``glofas_events`` for backwards compatibility (it is
# a stable historical identifier) but the analytical layer sees this source
# as ``Dartmouth_MasterList`` so it can be deduplicated against the HDX
# ``Dartmouth_FO`` vintage.
SOURCE = "Dartmouth_MasterList"
# Public DFO Global Active Archive of Large Floods (live MasterList).
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
        # Real Copernicus GloFAS reanalysis integration would go here: it
        # requires the cdsapi package and downloads NetCDF streamflow grids,
        # which would feed a separate raw table and normalizer. We leave a
        # clear placeholder rather than fabricate data.
        logger.info(
            "[%s] CDS credentials detected — a real GloFAS reanalysis "
            "ingester would write to its own raw table. Falling back to "
            "the public DFO MasterList for now.",
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
