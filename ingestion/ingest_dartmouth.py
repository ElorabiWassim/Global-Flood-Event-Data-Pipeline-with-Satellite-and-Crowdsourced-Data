"""
Ingest the Dartmouth Flood Observatory (DFO) "Global Active Archive of Large
Flood Events" into ``raw.dartmouth_events``.

Source: HDX shapefile (the only public format HDX still publishes for DFO):
    https://data.humdata.org/dataset/global-active-archive-of-large-flood-events-dfo

We download the SHP zip, read the ``.dbf`` attribute table with ``pyshp``
(pure-Python, no GDAL needed), and flatten each record into the field names
the existing transformer (``_normalize_dartmouth``) already understands.
The geometry is **discarded** — the .dbf already carries ``Centroid_X`` and
``Centroid_Y``, which is all we need.

The seed CSV (``data/raw/dartmouth/dartmouth_floods.csv``) remains as a
last-resort fallback for offline/disconnected runs.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import shapefile  # pyshp

from config.settings import HTTP_TIMEOUT
from db.client import insert_raw_records, log_ingestion, truncate_raw_table
from .common import new_batch_id, raw_subdir, sha256_file

logger = logging.getLogger(__name__)

SOURCE = "Dartmouth_FO"
DEFAULT_URL = (
    "https://data.humdata.org/dataset/1fd855de-57c6-42b3-83e1-9cf989b0f70d/"
    "resource/984cc240-b2b7-4266-9f61-5715a9e10ff5/"
    "download/wlf_nhr_fl_dfomasterlist_20190418.zip"
)
RAW_TABLE = "dartmouth_events"
SEED_FILE_NAME = "dartmouth_floods.csv"


def _flatten(rec: dict[str, Any]) -> dict[str, Any]:
    """Map DFO .dbf fields to the names the transformer expects.

    DBF field names are truncated to 10 chars per spec, hence the odd shapes
    (``Country__c``, ``Severity__``, ``Main_cause``, etc).
    """
    register = rec.get("Register__")
    return {
        # ---- fields the transformer reads -----------------------------------
        "ID": int(register) if isinstance(register, (int, float)) else register,
        "Began": rec.get("Began"),
        "Ended": rec.get("Ended"),
        "Country": rec.get("Country__c"),
        "MainCause": rec.get("Main_cause"),
        "Dead": rec.get("Dead"),
        "Displaced": rec.get("Displaced"),
        "Severity": rec.get("Severity__"),
        "latitude": rec.get("Centroid_Y"),
        "longitude": rec.get("Centroid_X"),
        "glide_number": (rec.get("Glide__") or None) or None,
        "event_name": rec.get("Detailed_L") or rec.get("Main_cause"),
        "source_url": (
            f"https://floodobservatory.colorado.edu/Events/{int(register)}/{int(register)}.html"
            if isinstance(register, (int, float))
            else None
        ),
        # ---- bonus fields for future use ------------------------------------
        "magnitude": rec.get("Magnitude"),
        "duration_days": rec.get("Duration_i"),
        "affected_sq_km": rec.get("Affected_s"),
        "other_countries": rec.get("Other"),
        "_raw": rec,  # full record for auditability
    }


def _download_zip(url: str, target: Path) -> bytes:
    """Download the SHP zip to disk and return the bytes for in-memory parsing."""
    target.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=HTTP_TIMEOUT, stream=True)
    r.raise_for_status()
    blob = r.content
    target.write_bytes(blob)
    return blob


def _read_shapefile_records(zip_bytes: bytes) -> list[dict[str, Any]]:
    """Open the SHP zip from memory and return one dict per .dbf record."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        shp_name = next(
            (n for n in zf.namelist() if n.lower().endswith(".shp")), None
        )
        if not shp_name:
            raise ValueError("HDX zip does not contain a .shp entry")
        base = shp_name[:-4]
        # We only need the .dbf for attributes, but pyshp expects shp+shx+dbf.
        with zf.open(base + ".shp") as shp, zf.open(base + ".dbf") as dbf, zf.open(
            base + ".shx"
        ) as shx:
            reader = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)
            return [r.as_dict() for r in reader.records()]


def _read_seed_csv(path: Path) -> list[dict[str, Any]]:
    """Read the bundled seed CSV (used when HDX is unreachable)."""
    df = pd.read_csv(path)
    df = df.where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def run(*, full_refresh: bool = True) -> int:
    """Run the Dartmouth ingestion. Returns rows inserted."""
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("dartmouth")
    target_dir = raw_subdir("dartmouth")
    seed_path = target_dir / SEED_FILE_NAME
    target_zip = target_dir / "dfo_masterlist.zip"
    snapshot_path = target_dir / f"dfo_records_{batch_id}.json"
    feed_url = os.getenv("DARTMOUTH_HDX_URL", DEFAULT_URL)

    records: list[dict[str, Any]]
    file_path: Path
    file_checksum: str | None = None
    fallback_used = False
    error_message: str | None = None

    try:
        zip_bytes = _download_zip(feed_url, target_zip)
        raw_records = _read_shapefile_records(zip_bytes)
        records = [_flatten(r) for r in raw_records]
        snapshot_path.write_text(
            json.dumps(raw_records, default=str, indent=2), encoding="utf-8"
        )
        file_path = target_zip
        file_checksum = sha256_file(target_zip)
        logger.info(
            "[%s] read %s records from HDX shapefile", SOURCE, len(records)
        )
    except (requests.RequestException, ValueError, zipfile.BadZipFile, OSError) as exc:
        error_message = f"HDX download/parse failed: {exc}"
        if not seed_path.exists():
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="failure",
                source_url=feed_url,
                message=error_message,
                started_at=started_at,
            )
            raise
        logger.warning("[%s] %s — falling back to seed CSV", SOURCE, error_message)
        records = _read_seed_csv(seed_path)
        file_path = seed_path
        file_checksum = sha256_file(seed_path)
        fallback_used = True

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
        file_checksum=file_checksum,
        message=("seed fallback used: " + (error_message or "")) if fallback_used else None,
        started_at=started_at,
    )
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    n = run()
    logger.info("Dartmouth ingestion complete (%s rows).", n)
