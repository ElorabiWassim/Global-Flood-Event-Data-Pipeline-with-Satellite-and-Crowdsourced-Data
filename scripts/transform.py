"""
Raw → staging transformation for the flood-event pipeline.

Reads each ``raw.<source>_events`` table, normalizes the payload into the
columns defined in ``schema.sql`` (``staging.flood_events``), generates an
H3 index and a PostGIS point geometry, and upserts the row keyed on
``(source, source_event_id)``.

The transformation is idempotent: a second run produces no duplicates and
updates fields in-place.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from typing import Any, Iterable

import h3
import pandas as pd
from sqlalchemy import text

from .config import H3_RESOLUTION
from .db import execute_with_retry, get_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _as_dict(payload: Any) -> dict:
    """Return the JSONB payload as a dict regardless of driver behavior."""
    if isinstance(payload, dict):
        return payload
    if payload is None:
        return {}
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return {}


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        # Strip thousands separators if pandas didn't
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_date(value: Any) -> datetime | None:
    """Parse a wide variety of input shapes (strings, datetime, numpy)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=False)
    except Exception:  # noqa: BLE001
        return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.to_pydatetime()


def _ymd_to_date(year: Any, month: Any, day: Any) -> datetime | None:
    y = _to_int(year)
    if not y:
        return None
    m = _to_int(month) or 1
    d = _to_int(day) or 1
    try:
        return datetime(y, max(1, min(12, m)), max(1, min(28, d)))
    except ValueError:
        return None


def _h3_for(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    try:
        return h3.geo_to_h3(lat, lon, H3_RESOLUTION)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Source-specific normalizers
#
# Each returns a list of dicts matching the staging.flood_events schema.
# ---------------------------------------------------------------------------
def _normalize_dartmouth(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        lat = _to_float(p.get("latitude"))
        lon = _to_float(p.get("longitude"))
        out.append(
            {
                "source": "Dartmouth_FO",
                "source_event_id": _clean_text(p.get("event_id"))
                or _clean_text(p.get("ID")),
                "event_name": _clean_text(p.get("event_name") or p.get("main_cause")),
                "main_cause": _clean_text(p.get("main_cause") or p.get("MainCause")),
                "date_start": _parse_date(p.get("date_start") or p.get("Began")),
                "date_end": _parse_date(p.get("date_end") or p.get("Ended")),
                "country": _clean_text(p.get("country") or p.get("Country")),
                "latitude": lat,
                "longitude": lon,
                "deaths": _to_int(p.get("deaths") or p.get("Dead")),
                "displaced": _to_int(p.get("displaced") or p.get("Displaced")),
                "affected": None,
                "severity": _to_float(p.get("severity") or p.get("Severity")),
                "flood_impact_index": None,
                "glide_number": _clean_text(p.get("glide_number")),
                "url": _clean_text(p.get("source_url")),
                "h3_index": _h3_for(lat, lon),
            }
        )
    return out


def _normalize_glofas(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        # Header may contain mojibake (legacy encoding). Try multiple keys.
        event_id = (
            p.get("ID")
            or p.get("Id")
            or p.get("#")
            or next(
                (v for k, v in p.items() if isinstance(k, str) and len(k) <= 3 and v),
                None,
            )
        )
        glide = next(
            (v for k, v in p.items() if isinstance(k, str) and "glide" in k.lower()),
            None,
        )
        impact = next(
            (
                v
                for k, v in p.items()
                if isinstance(k, str) and "impact" in k.lower() and "index" in k.lower()
            ),
            None,
        )
        out.append(
            {
                "source": "GloFAS",
                "source_event_id": _clean_text(event_id),
                "event_name": None,
                "main_cause": _clean_text(p.get("Main Cause")),
                "date_start": _parse_date(p.get("Start Date") or p.get("Began")),
                "date_end": _parse_date(p.get("End Date") or p.get("Ended")),
                "country": _clean_text(p.get("Country")),
                "latitude": None,
                "longitude": None,
                "deaths": _to_int(p.get("Fatalities") or p.get("Dead")),
                "displaced": _to_int(p.get("Displaced")),
                "affected": None,
                "severity": _to_float(p.get("Severity")),
                "flood_impact_index": _to_float(impact),
                "glide_number": _clean_text(glide),
                "url": "https://floodobservatory.colorado.edu/Archives/",
                "h3_index": None,
            }
        )
    return out


_EMS_CODE_RX = re.compile(r"EMSR\d+", re.IGNORECASE)


def _normalize_copernicus_ems(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        code = _clean_text(p.get("Code") or p.get("code"))
        date_str = p.get("Date") or p.get("date")
        # Activations CSV ships dates like "11/03/2026, 16:51"
        date_start = _parse_date(date_str)
        if date_start is None and isinstance(date_str, str):
            try:
                date_start = datetime.strptime(date_str.split(",")[0].strip(), "%d/%m/%Y")
            except ValueError:
                date_start = None
        url = (
            f"https://emergency.copernicus.eu/mapping/list-of-components/{code}"
            if code and _EMS_CODE_RX.match(code)
            else None
        )
        out.append(
            {
                "source": "Copernicus_EMS",
                "source_event_id": code,
                "event_name": _clean_text(p.get("Title")),
                "main_cause": _clean_text(p.get("Event Type")),
                "date_start": date_start,
                "date_end": None,
                "country": _clean_text(p.get("Country")),
                "latitude": None,
                "longitude": None,
                "deaths": None,
                "displaced": None,
                "affected": None,
                "severity": None,
                "flood_impact_index": None,
                "glide_number": None,
                "url": url,
                "h3_index": None,
            }
        )
    return out


def _normalize_emdat(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        lat = _to_float(p.get("Latitude"))
        lon = _to_float(p.get("Longitude"))
        date_start = _ymd_to_date(
            p.get("Start Year"), p.get("Start Month"), p.get("Start Day")
        )
        date_end = _ymd_to_date(p.get("End Year"), p.get("End Month"), p.get("End Day"))
        cause = _clean_text(p.get("Disaster Subtype")) or _clean_text(p.get("Disaster Type"))
        out.append(
            {
                "source": "EM-DAT",
                "source_event_id": _clean_text(p.get("DisNo.")),
                "event_name": _clean_text(p.get("Event Name")),
                "main_cause": cause,
                "date_start": date_start,
                "date_end": date_end,
                "country": _clean_text(p.get("Country")),
                "latitude": lat,
                "longitude": lon,
                "deaths": _to_int(p.get("Total Deaths")),
                "displaced": _to_int(p.get("No. Homeless")),
                "affected": _to_int(p.get("Total Affected") or p.get("No. Affected")),
                # EM-DAT "Magnitude" for floods is the inundated area in km², NOT
                # a normalized severity score, so it lives in flood_impact_index
                # and severity stays NULL (EM-DAT does not provide one).
                "severity": None,
                "flood_impact_index": _to_float(p.get("Magnitude")),
                "glide_number": None,
                "url": "https://public.emdat.be/",
                "h3_index": _h3_for(lat, lon),
            }
        )
    return out


def _normalize_reliefweb(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        lat = _to_float(p.get("latitude"))
        lon = _to_float(p.get("longitude"))
        out.append(
            {
                "source": "ReliefWeb",
                "source_event_id": _clean_text(p.get("event_id") or p.get("id")),
                "event_name": _clean_text(p.get("title") or p.get("name")),
                "main_cause": "Flood",
                "date_start": _parse_date(p.get("date") or p.get("date_created")),
                "date_end": _parse_date(p.get("date_end")),
                "country": _clean_text(p.get("country")),
                "latitude": lat,
                "longitude": lon,
                "deaths": None,
                "displaced": None,
                "affected": None,
                "severity": None,
                "flood_impact_index": None,
                "glide_number": _clean_text(p.get("glide_number")),
                "url": _clean_text(p.get("url")),
                "h3_index": _h3_for(lat, lon),
            }
        )
    return out


SOURCE_HANDLERS = {
    "dartmouth_events": _normalize_dartmouth,
    "glofas_events": _normalize_glofas,
    "copernicus_ems_events": _normalize_copernicus_ems,
    "emdat_events": _normalize_emdat,
    "reliefweb_events": _normalize_reliefweb,
}


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
UPSERT_SQL = text(
    """
    INSERT INTO staging.flood_events (
        source, source_event_id, event_name, main_cause,
        date_start, date_end,
        country, latitude, longitude, geometry,
        deaths, displaced, affected,
        severity, flood_impact_index,
        glide_number, url, h3_index, loaded_at
    ) VALUES (
        :source, :source_event_id, :event_name, :main_cause,
        :date_start, :date_end,
        :country, :latitude, :longitude,
        CASE
            WHEN :latitude IS NOT NULL AND :longitude IS NOT NULL
            THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
            ELSE NULL
        END,
        :deaths, :displaced, :affected,
        :severity, :flood_impact_index,
        :glide_number, :url, :h3_index, NOW()
    )
    ON CONFLICT (source, source_event_id) DO UPDATE SET
        event_name         = EXCLUDED.event_name,
        main_cause         = EXCLUDED.main_cause,
        date_start         = EXCLUDED.date_start,
        date_end           = EXCLUDED.date_end,
        country            = EXCLUDED.country,
        latitude           = EXCLUDED.latitude,
        longitude          = EXCLUDED.longitude,
        geometry           = EXCLUDED.geometry,
        deaths             = EXCLUDED.deaths,
        displaced          = EXCLUDED.displaced,
        affected           = EXCLUDED.affected,
        severity           = EXCLUDED.severity,
        flood_impact_index = EXCLUDED.flood_impact_index,
        glide_number       = EXCLUDED.glide_number,
        url                = EXCLUDED.url,
        h3_index           = EXCLUDED.h3_index,
        loaded_at          = NOW();
    """
)


def _upsert(rows: list[dict]) -> int:
    """Upsert normalized rows. Skips rows missing the required date_start."""
    valid = [r for r in rows if r.get("date_start") and r.get("source_event_id")]
    if not valid:
        return 0
    # Same Supabase-pooler-friendly chunk size as raw inserts. Each chunk runs
    # in its own auto-committed transaction with retry-on-OperationalError.
    chunk = 50
    inserted = 0
    for i in range(0, len(valid), chunk):
        slice_ = valid[i : i + chunk]
        execute_with_retry(UPSERT_SQL, slice_)
        inserted += len(slice_)
    return inserted


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def transform_source(table: str) -> int:
    """Transform a single raw table into staging.flood_events."""
    handler = SOURCE_HANDLERS[table]
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT payload FROM raw.{table}"))
        payloads = [_as_dict(row[0]) for row in result]
    if not payloads:
        logger.info("raw.%s is empty — skipping", table)
        return 0
    rows = handler(payloads)
    n = _upsert(rows)
    logger.info("Transformed raw.%s -> staging.flood_events: %s rows upserted", table, n)
    return n


def run_all() -> dict[str, int]:
    """Run the transformation for every source. Returns per-source counts."""
    results: dict[str, int] = {}
    for table in SOURCE_HANDLERS:
        try:
            results[table] = transform_source(table)
        except Exception:  # noqa: BLE001
            logger.exception("Transformation failed for raw.%s", table)
            results[table] = -1
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_all())
