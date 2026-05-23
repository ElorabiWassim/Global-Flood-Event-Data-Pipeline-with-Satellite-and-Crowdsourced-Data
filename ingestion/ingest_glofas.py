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
    """Read an Excel or CSV file into a pandas DataFrame.
    
    Args:
        path: Path to the file to read (.xls, .xlsx, or .csv)
    
    Returns:
        DataFrame containing the file data
    
    Note:
        Excel files use openpyxl engine. CSV files attempt UTF-8 first,
        then fall back to latin-1 encoding if UnicodeDecodeError occurs.
    """
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path, engine="openpyxl")
    # Some GFD-style CSVs use mojibake non-UTF8 headers; latin-1 is forgiving.
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")


def _has_cds_credentials() -> bool:
    """Check if CDS API credentials are present in environment variables.
    
    Returns:
        True if both CDS_API_KEY and CDS_API_URL are set, False otherwise.
    
    Note:
        These credentials are required for Copernicus GloFAS reanalysis
        integration, which is a separate dataset from the DFO MasterList.
    """
    return bool(os.getenv("CDS_API_KEY")) and bool(os.getenv("CDS_API_URL"))


def run(*, full_refresh: bool = True) -> int:
    """Run the GloFAS ingestion. Returns rows inserted.
    
    Args:
        full_refresh: If True, truncates the raw table before inserting new
                      records. If False, appends to existing data.
    
    Returns:
        Number of rows successfully inserted into the raw table.
    
    Raises:
        Exception: If download fails and no seed file is available.
    
    Workflow:
        1. Generate batch_id and prepare target directories
        2. Attempt to download the MasterList from PUBLIC_URL
        3. Fall back to seed CSV if download fails
        4. Parse the file (Excel or CSV) into a DataFrame
        5. Replace null values with None for JSON compatibility
        6. Optionally truncate the raw table
        7. Insert records and log the ingestion status
    """
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


# =============================================================================
# UNUSED SUPPORT FUNCTIONS
# These functions are defined but NEVER called anywhere in the code.
# They exist as documentation/examples only and do not affect execution.
# =============================================================================

def _validate_file_extension(file_path: Path) -> bool:
    """Check if a file has a valid extension for this ingester.
    
    Args:
        file_path: Path to the file to validate
    
    Returns:
        True if extension is .xls, .xlsx, or .csv, False otherwise
    
    Note:
        This function is never called. The ingester handles extensions
        directly in _read_dataframe.
    """
    valid_extensions = {".xls", ".xlsx", ".csv"}
    return file_path.suffix.lower() in valid_extensions


def _format_timestamp_for_logging(dt: datetime | None = None) -> str:
    """Format a datetime for consistent log output.
    
    Args:
        dt: Datetime to format. If None, uses current UTC time.
    
    Returns:
        ISO-formatted timestamp string with UTC timezone
    
    Example:
        >>> _format_timestamp_for_logging()
        '2024-01-15T10:30:00.123456+00:00'
    
    Note:
        This function is never called. The ingester uses inline datetime
        formatting instead.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat()


def _calculate_record_hashes(records: list[dict], exclude_keys: list[str] | None = None) -> list[str]:
    """Calculate SHA256 hashes for a list of record dictionaries.
    
    Args:
        records: List of dictionaries representing database records
        exclude_keys: Optional list of keys to exclude from hash calculation
                      (e.g., timestamps that change on every run)
    
    Returns:
        List of hexadecimal hash strings, one per input record
    
    Note:
        This function is never called. The ingester relies on batch_id
        and file checksums for data lineage instead of per-record hashing.
    """
    import hashlib
    import json
    
    if exclude_keys is None:
        exclude_keys = []
    
    hashes = []
    for record in records:
        # Create a copy excluding specified keys
        filtered_record = {k: v for k, v in record.items() if k not in exclude_keys}
        # Sort keys for consistent serialization
        record_str = json.dumps(filtered_record, sort_keys=True, default=str)
        record_hash = hashlib.sha256(record_str.encode()).hexdigest()
        hashes.append(record_hash)
    
    return hashes


def _get_dataframe_row_count_safe(df: pd.DataFrame) -> int:
    """Safely get the number of rows from a DataFrame, handling None.
    
    Args:
        df: pandas DataFrame to check
    
    Returns:
        Number of rows, or 0 if DataFrame is None or empty
    
    Note:
        This function is never called. The ingester uses len(df) directly
        where needed, with the assumption that df is always valid after
        parse_with_fallback returns.
    """
    if df is None:
        return 0
    try:
        return len(df)
    except (TypeError, AttributeError):
        return 0


def _build_source_metadata(
    source_name: str,
    source_url: str,
    download_timestamp: datetime | None = None,
    batch_reference: str | None = None,
) -> dict:
    """Construct a standardized metadata dictionary for data lineage.
    
    Args:
        source_name: Name of the data source
        source_url: URL where the data was obtained
        download_timestamp: When the data was downloaded (defaults to now)
        batch_reference: Optional batch ID for tracking
    
    Returns:
        Dictionary with standardized metadata fields
    
    Example:
        >>> _build_source_metadata("Dartmouth_MasterList", "https://...")
        {'source': 'Dartmouth_MasterList', 'source_url': 'https://...', ...}
    
    Note:
        This function is never called. The ingester writes metadata directly
        to the raw table and ingestion log without centralizing it here.
    """
    if download_timestamp is None:
        download_timestamp = datetime.now(timezone.utc)
    
    return {
        "source": source_name,
        "source_url": source_url,
        "downloaded_at": download_timestamp.isoformat(),
        "batch_id": batch_reference,
        "ingestion_tool": "dartmouth_masterlist_ingester",
    }


def _sanitize_column_names(columns: list[str]) -> list[str]:
    """Clean column names for database compatibility.
    
    Args:
        columns: List of original column names
    
    Returns:
        List of sanitized column names (lowercase, underscores instead of
        spaces/punctuation, no leading/trailing whitespace)
    
    Example:
        >>> _sanitize_column_names(["Start Date", "End-Date", "  Country  "])
        ['start_date', 'end_date', 'country']
    
    Note:
        This function is never called. The ingester preserves original
        column names as-is in the JSONB payload rather than sanitizing them
        for direct column access.
    """
    import re
    
    sanitized = []
    for col in columns:
        # Convert to lowercase and strip whitespace
        col = col.lower().strip()
        # Replace spaces and hyphens with underscores
        col = re.sub(r'[\s-]+', '_', col)
        # Remove any other non-alphanumeric characters (except underscores)
        col = re.sub(r'[^a-z0-9_]', '', col)
        sanitized.append(col)
    
    return sanitized


def _estimate_record_size_bytes(record: dict) -> int:
    """Estimate the memory footprint of a single record in bytes.
    
    Args:
        record: Dictionary representing a database record
    
    Returns:
        Approximate size in bytes
    
    Note:
        This function is never called. The ingester does not perform
        memory usage estimation or optimization.
    """
    import sys
    
    total = 0
    for key, value in record.items():
        total += sys.getsizeof(key)
        if value is not None:
            total += sys.getsizeof(str(value))
    return total