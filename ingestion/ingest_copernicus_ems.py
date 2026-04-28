"""
Ingest the Copernicus Emergency Management Service (EMS) Rapid Mapping
activations into ``raw.copernicus_ems_events``.

Source (paged JSON API):
    https://mapping.emergency.copernicus.eu/activations/api/activations/

The endpoint is unauthenticated, returns 20 results per page, and exposes a
DRF-style cursor under ``next``. Each result looks roughly like::

    {
        "code": "EMSR871",
        "name": "Flood in Abruzzo, Molise and Basilicata regions, Italy",
        "category": {"slug": "flood", "name": "Flood"},
        "countries": [{"short_name": "Italy"}],
        "centroid": "POINT (14.93 41.89)",
        "activationTime": "2026-04-01T15:16:00",
        "lastUpdate": "2026-04-09T11:50:23.212284",
        "drmPhase": "response",
        "closed": true,
        "n_aois": 6,
        "n_products": 15,
        "search_snippet": "From March 31st in the afternoon, ..."
    }

To keep ``transformations.transform._normalize_copernicus_ems`` and its tests
untouched, this module flattens each activation into the field names the
transformer already understands (``Code``, ``Title``, ``Event Type``,
``Date``, ``Country``) and stashes the original payload under ``_raw``.

If the API is unreachable, we fall back to the seed CSV
``data/raw/copernicus_ems/activations.csv``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config.settings import HTTP_TIMEOUT
from db.client import insert_raw_records, log_ingestion, truncate_raw_table
from .common import new_batch_id, parse_with_fallback, raw_subdir, sha256_file

logger = logging.getLogger(__name__)

SOURCE = "Copernicus_EMS"
DEFAULT_API_URL = "https://mapping.emergency.copernicus.eu/activations/api/activations/"
RAW_TABLE = "copernicus_ems_events"
SEED_FILE_NAME = "activations.csv"

# Page size hint (the server may cap below this); also guards against
# infinite loops on a misbehaving endpoint.
PAGE_LIMIT = int(os.getenv("COPERNICUS_EMS_PAGE_LIMIT", "100"))
MAX_PAGES = int(os.getenv("COPERNICUS_EMS_MAX_PAGES", "100"))

# WKT POINT regex: "POINT (lon lat)" — Copernicus stores lon/lat in that order.
_WKT_POINT_RX = re.compile(
    r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", re.IGNORECASE
)


def _parse_centroid(centroid: str | None) -> tuple[float | None, float | None]:
    """Return (latitude, longitude) from a WKT POINT string, or (None, None)."""
    if not isinstance(centroid, str):
        return None, None
    m = _WKT_POINT_RX.search(centroid)
    if not m:
        return None, None
    try:
        lon, lat = float(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        return None, None
    return lat, lon


def _flatten(activation: dict[str, Any]) -> dict[str, Any]:
    """Map the API JSON shape to the column names the transformer expects."""
    countries = activation.get("countries") or []
    country = ", ".join(
        c.get("short_name") for c in countries if c and c.get("short_name")
    ) or None
    category = activation.get("category") or {}
    lat, lon = _parse_centroid(activation.get("centroid"))
    return {
        # ---- fields the transformer reads -----------------------------------
        "Code": activation.get("code"),
        "Title": activation.get("name"),
        "Event Type": category.get("name"),
        "Date": activation.get("activationTime"),
        "Country": country,
        # ---- bonus fields available for future use --------------------------
        "Latitude": lat,
        "Longitude": lon,
        "category_slug": category.get("slug"),
        "drm_phase": activation.get("drmPhase"),
        "closed": activation.get("closed"),
        "last_update": activation.get("lastUpdate"),
        "n_aois": activation.get("n_aois"),
        "n_products": activation.get("n_products"),
        # Keep the original payload for auditability.
        "_raw": activation,
    }


def _fetch_activations(
    api_url: str = DEFAULT_API_URL,
    *,
    page_limit: int = PAGE_LIMIT,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """Page through the activations API, returning *only* flood activations.

    We filter on ``category.slug == 'flood'`` (the API's canonical key) which
    matches the previous CSV-era behaviour where rows were filtered by
    ``Event Type contains 'Flood'``.
    """
    session = requests.Session()
    session.headers.update(
        {"Accept": "application/json", "User-Agent": "flood-event-pipeline/1.0"}
    )
    out: list[dict[str, Any]] = []
    url: str | None = api_url
    params: dict[str, Any] | None = {"limit": page_limit}
    pages = 0
    while url and pages < max_pages:
        r = session.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        for item in payload.get("results", []) or []:
            slug = ((item.get("category") or {}).get("slug") or "").lower()
            if slug == "flood":
                out.append(item)
        url = payload.get("next")
        params = None  # the `next` URL already carries limit + offset
        pages += 1
    logger.info(
        "[%s] fetched %s flood activations across %s page(s)", SOURCE, len(out), pages
    )
    return out


def _read_seed_csv(path: Path) -> pd.DataFrame:
    """Read the seed activations.csv (auto-detect ';' or ',' delimiter)."""
    try:
        return pd.read_csv(path, sep=None, engine="python")
    except UnicodeDecodeError:
        return pd.read_csv(path, sep=None, engine="python", encoding="latin-1")


def run(*, full_refresh: bool = True) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("copernicus_ems")
    target_dir = raw_subdir("copernicus_ems")
    seed_path = target_dir / SEED_FILE_NAME
    snapshot_path = target_dir / f"activations_{batch_id}.json"
    feed_url = os.getenv("COPERNICUS_EMS_FEED_URL", DEFAULT_API_URL)

    records: list[dict[str, Any]]
    file_path: Path
    file_checksum: str | None = None
    fallback_used = False
    error_message: str | None = None

    try:
        activations = _fetch_activations(feed_url)
        records = [_flatten(a) for a in activations]
        snapshot_path.write_text(json.dumps(activations, indent=2), encoding="utf-8")
        file_path = snapshot_path
        file_checksum = sha256_file(snapshot_path)
    except (requests.RequestException, ValueError) as exc:
        error_message = f"API call failed: {exc}"
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
        df, parser_fallback, parser_msg = parse_with_fallback(
            _read_seed_csv, seed_path, None, source=SOURCE
        )
        if "Event Type" in df.columns:
            df = df[
                df["Event Type"].astype(str).str.contains("Flood", case=False, na=False)
            ]
        df = df.where(pd.notnull(df), None)
        records = df.to_dict(orient="records")
        file_path = seed_path
        file_checksum = sha256_file(seed_path)
        fallback_used = True
        if parser_fallback:
            error_message = (error_message or "") + f"; parser fallback: {parser_msg}"

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
    logger.info("Copernicus EMS ingestion complete (%s rows).", n)
