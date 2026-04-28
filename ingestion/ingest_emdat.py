"""
Ingest EM-DAT (CRED / UCLouvain) flood disasters into ``raw.emdat_events``.

Source priority order:

1. ``EMDAT_DOWNLOAD_URL`` env var — operator-supplied signed URL or
   institutional mirror of the per-event EM-DAT export. If set, it wins.
2. The public CRED-published HDX dataset
   ``emdat-country-profiles`` (default) — a global XLSX with
   ``(Year × Country × Disaster Subtype)`` *aggregated* yearly stats. This is
   the only fully-public, no-login source. Each aggregate row is expanded
   into a synthetic per-event record so the rest of the pipeline (which
   expects ``DisNo.``, ``Start Year/Month/Day``, etc.) keeps working.
3. The bundled seed CSV ``data/raw/emdat/emdat_floods.csv`` — last resort
   for offline runs.

Data caveat (HDX path): the synthetic events have year-only date precision,
no ``Latitude/Longitude``, no ``River Basin``, and ``DisNo.`` of the form
``EMDAT-HDX-{Year}-{ISO}-{slug(Subtype)}``. Damage / Deaths / Affected come
straight from EM-DAT's totals.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

SOURCE = "EM-DAT"
RAW_TABLE = "emdat_events"
SEED_FILE_NAME = "emdat_floods.csv"

# CRED-published global EM-DAT aggregate on HDX (XLSX, ~400 KB, weekly refresh).
DEFAULT_HDX_URL = (
    "https://data.humdata.org/dataset/74163686-a029-4e27-8fbf-c5bfcd13f953/"
    "resource/c5ce40d6-07b1-4f36-955a-d6196436ff6b/"
    "download/emdat-country-profiles_2026_04_24.xlsx"
)

# Columns that uniquely identify the HDX aggregate schema.
_HDX_MARKER_COLS = {"Year", "Country", "ISO", "Disaster Type"}
# Columns that uniquely identify the per-event EM-DAT export schema.
_PER_EVENT_MARKER_COLS = {"DisNo.", "Start Year"}

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def _slug(value: Any) -> str:
    """Lowercase + collapse non-alphanumerics to '-' for synthetic event IDs."""
    if value is None:
        return "unknown"
    return _SLUG_RX.sub("-", str(value).lower()).strip("-") or "unknown"


def _read_dataframe(path: Path) -> pd.DataFrame:
    """Read XLSX or CSV (with encoding fallback) into a DataFrame."""
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")


def _is_hdx_aggregate(df: pd.DataFrame) -> bool:
    return _HDX_MARKER_COLS.issubset(df.columns)


def _is_per_event(df: pd.DataFrame) -> bool:
    return _PER_EVENT_MARKER_COLS.issubset(df.columns)


def _explode_hdx_aggregate(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Turn one ``(Year, Country, Subtype)`` aggregate row into a synthetic
    per-event record matching the field names the transformer reads
    (``DisNo.``, ``Start Year/Month/Day``, ``End Year/Month/Day``,
    ``Country``, ``Disaster Type``, ``Disaster Subtype``, ``Total Deaths``,
    ``Total Affected``, ``Event Name``).

    Date precision is **year-only** by construction; we set
    ``Start = Jan 1`` and ``End = Dec 31`` of that year so downstream date
    parsing succeeds.
    """
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        year = row.get("Year")
        country = row.get("Country")
        iso = row.get("ISO")
        subtype = row.get("Disaster Subtype")
        dtype = row.get("Disaster Type")
        try:
            year_int = int(year) if pd.notna(year) else None
        except (TypeError, ValueError):
            year_int = None
        if year_int is None:
            continue
        # Synthetic but stable + unique disaster ID.
        disno = f"EMDAT-HDX-{year_int}-{(iso or _slug(country)).upper()}-{_slug(subtype or dtype)}"
        out.append(
            {
                "DisNo.": disno,
                "Start Year": year_int,
                "Start Month": 1,
                "Start Day": 1,
                "End Year": year_int,
                "End Month": 12,
                "End Day": 31,
                "Country": country,
                "ISO": iso,
                "Disaster Group": row.get("Disaster Group"),
                "Disaster Subgroup": row.get("Disaster Subroup"),  # sic
                "Disaster Type": dtype,
                "Disaster Subtype": subtype,
                "Event Name": f"{subtype or dtype} events in {country}, {year_int}",
                "Total Events": row.get("Total Events"),
                "Total Deaths": row.get("Total Deaths"),
                "Total Affected": row.get("Total Affected"),
                "Total Damage (USD, original)": row.get("Total Damage (USD, original)"),
                "Total Damage (USD, adjusted)": row.get("Total Damage (USD, adjusted)"),
                # Per-event fields not available in aggregate data.
                "Latitude": None,
                "Longitude": None,
                "River Basin": None,
                "Magnitude": None,
                "No. Homeless": None,
                # Audit hint so consumers know this is aggregated, not per-event.
                "_aggregate_source": "HDX:emdat-country-profiles",
            }
        )
    return out


def run(*, full_refresh: bool = True) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("emdat")
    target_dir = raw_subdir("emdat")
    seed_path = target_dir / SEED_FILE_NAME
    download_url = os.getenv("EMDAT_DOWNLOAD_URL", DEFAULT_HDX_URL)
    # Preserve original extension so _read_dataframe picks the right reader.
    suffix = ".xlsx" if download_url.lower().endswith((".xlsx", ".xls")) else ".csv"
    target_file = target_dir / f"emdat_latest{suffix}"

    download: DownloadResult | None = None
    file_path: Path

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

    df, parser_fallback, parser_msg = parse_with_fallback(
        _read_dataframe,
        file_path,
        seed_path if seed_path.exists() and seed_path != file_path else None,
        source=SOURCE,
    )
    if parser_fallback:
        file_path = seed_path

    # Filter to flood-typed disasters in either schema.
    if "Disaster Type" in df.columns:
        df = df[
            df["Disaster Type"].astype(str).str.contains("Flood", case=False, na=False)
        ]
    df = df.where(pd.notnull(df), None)

    # Normalize to per-event-shaped records the transformer can read.
    if _is_hdx_aggregate(df):
        records = _explode_hdx_aggregate(df)
        logger.info(
            "[%s] HDX aggregate detected — exploded %s flood-year-country rows "
            "into synthetic per-event records",
            SOURCE,
            len(records),
        )
    elif _is_per_event(df):
        records = df.to_dict(orient="records")
        logger.info(
            "[%s] per-event EM-DAT export detected — passing %s rows through",
            SOURCE,
            len(records),
        )
    else:
        # Unknown schema: pass through verbatim. The transformer will skip rows
        # without a valid date_start, so this fails gracefully.
        records = df.to_dict(orient="records")
        logger.warning(
            "[%s] unknown EM-DAT schema; passing %s rows through verbatim. Columns: %s",
            SOURCE,
            len(records),
            list(df.columns),
        )

    if full_refresh:
        truncate_raw_table(RAW_TABLE)
    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=download_url,
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=download_url,
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
