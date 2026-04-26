"""
Ingest disaster events from the public ReliefWeb API into
``raw.reliefweb_events``.

ReliefWeb provides a public REST API that does not require an API key — only
a polite ``appname`` parameter:

    https://api.reliefweb.int/v1/disasters?appname=<your-app>&filter[field]=type&filter[value]=Flood

We pull paginated results, persist the raw JSON sidecar and the CSV-flattened
view, and insert one JSONB row per disaster into the raw schema.
A seed CSV (``reliefweb_floods.csv``) is used as a last-resort fallback so the
pipeline can still run when the API is unreachable.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ..config import HTTP_TIMEOUT, RELIEFWEB_APPNAME
from ..db import insert_raw_records, log_ingestion, truncate_raw_table
from .common import new_batch_id, raw_subdir, sha256_file

logger = logging.getLogger(__name__)

SOURCE = "ReliefWeb"
API_URL = "https://api.reliefweb.int/v1/disasters"
RAW_TABLE = "reliefweb_events"
SEED_FILE_NAME = "reliefweb_floods.csv"

# Request a generous slice of recent floods. ReliefWeb caps limit at 1000.
DEFAULT_LIMIT = int(__import__("os").getenv("RELIEFWEB_LIMIT", "500"))


def _fetch_disasters(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Page through ReliefWeb disasters filtered to flood events."""
    params = {
        "appname": RELIEFWEB_APPNAME,
        "profile": "list",
        "preset": "latest",
        "filter[field]": "type",
        "filter[value]": "Flood",
        "limit": min(limit, 1000),
    }
    r = requests.get(API_URL, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    return payload.get("data", [])


def _disasters_to_records(disasters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the ReliefWeb response into one dict per disaster."""
    out: list[dict[str, Any]] = []
    for d in disasters:
        fields = d.get("fields", {}) or {}
        countries = fields.get("country") or []
        primary_country = countries[0] if countries else {}
        out.append(
            {
                "source": SOURCE,
                "event_id": d.get("id"),
                "title": fields.get("name"),
                "date": (fields.get("date") or {}).get("created"),
                "country": primary_country.get("name"),
                "iso3": (primary_country.get("iso3") or "").lower() or None,
                "status": fields.get("status"),
                "glide_number": fields.get("glide"),
                "url": fields.get("url"),
                "raw": fields,  # keep the rest of the response for auditability
            }
        )
    return out


def run(*, full_refresh: bool = True, limit: int = DEFAULT_LIMIT) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("reliefweb")
    target_dir = raw_subdir("reliefweb")
    seed_path = target_dir / SEED_FILE_NAME
    snapshot_path = target_dir / f"reliefweb_{batch_id}.json"

    records: list[dict[str, Any]]
    file_path: Path
    file_checksum: str | None = None
    fallback_used = False
    error_message: str | None = None

    try:
        disasters = _fetch_disasters(limit=limit)
        records = _disasters_to_records(disasters)
        snapshot_path.write_text(json.dumps(disasters, indent=2), encoding="utf-8")
        file_path = snapshot_path
        file_checksum = sha256_file(snapshot_path)
        logger.info("[%s] fetched %s disasters from API", SOURCE, len(records))
    except (requests.RequestException, ValueError) as exc:
        error_message = f"API call failed: {exc}"
        if not seed_path.exists():
            log_ingestion(
                batch_id=batch_id,
                source=SOURCE,
                status="failure",
                source_url=API_URL,
                message=error_message,
                started_at=started_at,
            )
            raise
        logger.warning("[%s] %s — falling back to seed CSV", SOURCE, error_message)
        df = pd.read_csv(seed_path)
        df = df.where(pd.notnull(df), None)
        records = df.to_dict(orient="records")
        file_path = seed_path
        file_checksum = sha256_file(seed_path)
        fallback_used = True

    if full_refresh:
        truncate_raw_table(RAW_TABLE)
    rows = insert_raw_records(
        RAW_TABLE,
        records,
        source=SOURCE,
        source_url=API_URL,
        file_path=str(file_path),
        batch_id=batch_id,
    )
    log_ingestion(
        batch_id=batch_id,
        source=SOURCE,
        status="success",
        rows_ingested=rows,
        source_url=API_URL,
        file_path=str(file_path),
        file_checksum=file_checksum,
        message=("seed fallback used: " + (error_message or "")) if fallback_used else None,
        started_at=started_at,
    )
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    n = run()
    logger.info("ReliefWeb ingestion complete (%s rows).", n)
