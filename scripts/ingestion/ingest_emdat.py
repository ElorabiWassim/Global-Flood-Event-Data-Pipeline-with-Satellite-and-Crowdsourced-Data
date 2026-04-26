"""
Ingest EM-DAT (CRED / UCLouvain) flood disasters into raw.emdat_events.

EM-DAT REQUIRES a free user account at https://public.emdat.be — bulk
downloads are gated behind a session cookie / token. There is therefore no
fully-public direct URL.

This module:

1. Looks for the env var ``EMDAT_DOWNLOAD_URL`` (operator can supply a signed
   URL or institutional mirror).
2. Falls back to a seed CSV (``data/raw/emdat/emdat_floods.csv``) shipped
   with the repository so the pipeline can run end-to-end.

The Excel/CSV columns are preserved verbatim in the JSONB payload.
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

SOURCE = "EM-DAT"
RAW_TABLE = "emdat_events"
SEED_FILE_NAME = "emdat_floods.csv"


def _read_dataframe(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")


def run(*, full_refresh: bool = True) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("emdat")
    target_dir = raw_subdir("emdat")
    seed_path = target_dir / SEED_FILE_NAME
    download_url = os.getenv("EMDAT_DOWNLOAD_URL")
    target_file = target_dir / "emdat_latest.csv"

    download: DownloadResult | None = None
    file_path: Path

    if download_url:
        try:
            download = download_to(
                source=SOURCE,
                url=download_url,
                target=target_file,
                fallback=seed_path if seed_path.exists() else None,
            )
            file_path = download.file_path
        except Exception as exc:  # noqa: BLE001
            if seed_path.exists():
                logger.warning("EM-DAT download failed (%s); using seed CSV", exc)
                file_path = seed_path
            else:
                log_ingestion(
                    batch_id=batch_id,
                    source=SOURCE,
                    status="failure",
                    source_url=download_url,
                    message=f"download error and no seed available: {exc}",
                    started_at=started_at,
                )
                raise
    else:
        if not seed_path.exists():
            msg = (
                "EM-DAT requires registration. Set EMDAT_DOWNLOAD_URL or place "
                f"a CSV at {seed_path} (see docs/data_sources.md)."
            )
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="skipped",
                message=msg,
                started_at=started_at,
            )
            logger.warning(msg)
            return 0
        logger.info("[%s] no EMDAT_DOWNLOAD_URL — using seed CSV %s", SOURCE, seed_path)
        file_path = seed_path

    df, parser_fallback, parser_msg = parse_with_fallback(
        _read_dataframe,
        file_path,
        seed_path if seed_path.exists() and seed_path != file_path else None,
        source=SOURCE,
    )
    if parser_fallback:
        file_path = seed_path
    # Keep only flood-typed disasters if the column exists.
    if "Disaster Type" in df.columns:
        df = df[df["Disaster Type"].astype(str).str.contains("Flood", case=False, na=False)]
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if full_refresh:
        truncate_raw_table(RAW_TABLE)
    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=download_url or "seed:bundled-emdat-csv",
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=download_url or "seed:bundled-emdat-csv",
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
    logger.info("EM-DAT ingestion complete (%s rows).", n)
