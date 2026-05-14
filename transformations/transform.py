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
from collections.abc import Iterable as IterableABC
from datetime import datetime
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import text

try:
    import h3
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    h3 = None

from config.settings import H3_RESOLUTION
from db.client import execute_with_retry, get_engine
from transformations.social_geo import extract_location

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
    if h3 is None:
        logger.warning("h3 package is not installed; h3_index will be NULL")
        return None
    try:
        return h3.geo_to_h3(lat, lon, H3_RESOLUTION)
    except Exception:  # noqa: BLE001
        return None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, IterableABC):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return []


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Source-specific normalizers
#
# Each returns a list of dicts matching the staging.flood_events schema.
# ---------------------------------------------------------------------------
def _is_named_basin(value: str | None) -> bool:
    """Return True only when the value looks like a real basin name.

    EM-DAT's ``River Basin`` column is dirty: about 12% of populated rows
    contain area-like numbers (e.g. ``1190``, ``43710``, ``686734.8``) instead
    of names. We reject pure-numeric / mostly-numeric strings to keep the
    ``staging.flood_events.river_basin`` column trustworthy.
    """
    if not value:
        return False
    s = value.strip()
    return bool(s) and any(c.isalpha() for c in s)


def _find_basin(p: dict) -> str | None:
    """Defensive search for a basin field across heterogeneous source schemas.

    EM-DAT exposes ``River Basin``; some GFD / DFO records include ``Basin``
    or ``MainCause`` containing a basin name. We accept any key that contains
    "basin" (case-insensitive) and is not the inundation-area metric, and
    only return values that look like a real name.
    """
    for k, v in p.items():
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if "basin" in kl and "area" not in kl:
            cleaned = _clean_text(v)
            if _is_named_basin(cleaned):
                return cleaned
    return None


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
                "river_basin": _find_basin(p),
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
                # NOTE: this raw table is named ``glofas_events`` for legacy
                # reasons but actually carries the Dartmouth Flood Observatory
                # live ``MasterList`` (Brakenridge / DFO). The label below is
                # the canonical source name used by the analytical layer; the
                # ``marts.flood_events_unique`` view dedupes Register# overlap
                # against the HDX-frozen ``Dartmouth_FO`` source.
                "source": "Dartmouth_MasterList",
                "source_event_id": _clean_text(event_id),
                "event_name": None,
                "main_cause": _clean_text(p.get("Main Cause")),
                "date_start": _parse_date(p.get("Start Date") or p.get("Began")),
                "date_end": _parse_date(p.get("End Date") or p.get("Ended")),
                "country": _clean_text(p.get("Country")),
                "river_basin": _find_basin(p),
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
                "river_basin": _find_basin(p),
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
                "river_basin": (
                    _clean_text(p.get("River Basin"))
                    if _is_named_basin(_clean_text(p.get("River Basin")))
                    else _find_basin(p)
                ),
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
                "river_basin": _find_basin(p),
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


def _current_exclusions(text: str) -> list[str]:
    """Re-evaluate the current Bluesky exclusion rules against arbitrary text.

    Used at the raw-to-staging step so tightening
    `ingestion.ingest_bluesky.DEFAULT_EXCLUDED_PHRASES` (or the political
    metaphor regex) retroactively drops already-ingested false positives on
    the next transform. Imported lazily because the ingestion module imports
    the requests library which we do not want pulled in for unrelated
    transformer use.
    """
    if not text:
        return []
    try:
        from ingestion.ingest_bluesky import _excluded_keywords as _ek
    except Exception:  # noqa: BLE001
        return []
    try:
        return _ek(text)
    except Exception:  # noqa: BLE001
        return []


def _social_flood_relevance_score(payload: dict) -> float:
    text = _clean_text(payload.get("text")) or ""
    matched = _as_text_list(payload.get("matched_keywords"))
    context_terms = _as_text_list(payload.get("matched_context_terms"))
    strong_terms = _as_text_list(payload.get("matched_strong_terms"))
    # Trust upstream exclusions AND re-check the text against the current
    # exclusion rules so a tightened filter retroactively suppresses old
    # raw rows on the next transform pass.
    excluded = _as_text_list(payload.get("excluded_keywords")) + _current_exclusions(text)
    if not text or not matched or excluded:
        return 0.0
    upstream_filter_score = _to_float(payload.get("filter_score"))
    base = upstream_filter_score if upstream_filter_score is not None else 0.4
    base += min(len(matched), 4) * 0.06
    base += min(len(context_terms), 5) * 0.04
    if strong_terms:
        base += 0.12
    disaster_terms = (
        "evacuat",
        "rescue",
        "river",
        "rain",
        "storm",
        "road",
        "bridge",
        "emergency",
        "damage",
        "inondation",
        "crue",
        "\u0633\u064a\u0648\u0644",
        "\u0641\u064a\u0636\u0627\u0646",
    )
    folded = text.casefold()
    if any(term in folded for term in disaster_terms):
        base += 0.08
    return _clamp_score(base)


def _social_source_confidence(payload: dict) -> float:
    """How much trust to assign to the collection channel itself.

    This is not truth confirmation. It only says the record came from a public
    platform API/search path and retained a stable post identifier.
    """
    explicit = _to_float(payload.get("source_confidence"))
    if explicit is not None:
        return _clamp_score(explicit)
    if _clean_text(payload.get("platform")) and _clean_text(payload.get("post_id")):
        return 0.6
    return 0.3


def _social_location_confidence(payload: dict) -> float:
    lat = _to_float(payload.get("latitude"))
    lon = _to_float(payload.get("longitude"))
    if lat is not None and lon is not None:
        return 1.0
    if _clean_text(payload.get("place_name")) and _clean_text(payload.get("country")):
        return 0.75
    if _clean_text(payload.get("country")):
        return 0.5
    if _clean_text(payload.get("place_name")):
        return 0.35
    return 0.0


def _social_signal_confidence(payload: dict) -> float:
    relevance = _social_flood_relevance_score(payload)
    if relevance <= 0:
        return 0.0
    location = _social_location_confidence(payload)
    source = _social_source_confidence(payload)
    recency = 1.0 if _parse_date(payload.get("created_at")) else 0.0
    return _clamp_score(
        (relevance * 0.5) + (location * 0.25) + (source * 0.15) + (recency * 0.1)
    )


def _normalize_social_media_posts(payloads: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        lat = _to_float(p.get("latitude"))
        lon = _to_float(p.get("longitude"))
        created_at = _parse_date(p.get("created_at"))
        post_id = _clean_text(p.get("post_id"))
        platform = _clean_text(p.get("platform"))
        matched = _as_text_list(p.get("matched_keywords"))
        # Merge upstream exclusions with current-rule exclusions so tightening
        # the filter retroactively flags previously-ingested false positives.
        text_for_check = _clean_text(p.get("text")) or ""
        excluded = _as_text_list(p.get("excluded_keywords")) + _current_exclusions(text_for_check)
        # De-duplicate while preserving order so the audit trail stays readable.
        seen: set[str] = set()
        excluded = [x for x in excluded if not (x in seen or seen.add(x))]
        # Country and place_name are usually missing from Bluesky posts.
        # Fill them in defensively from the post text, but never overwrite an
        # explicit value that the ingester already carried through.
        country = _clean_text(p.get("country"))
        place_name = _clean_text(p.get("place_name"))
        if not country or not place_name:
            inferred = extract_location(text_for_check)
            if not country:
                country = inferred.get("country")
            if not place_name:
                place_name = inferred.get("place_name")
        # Recompute location confidence after enrichment so the staging score
        # rewards the newly-inferred country/place_name.
        location_payload = {
            **p,
            "country": country,
            "place_name": place_name,
            "latitude": lat,
            "longitude": lon,
        }
        relevance = _social_flood_relevance_score(p)
        location_confidence = _social_location_confidence(location_payload)
        signal_confidence = _social_signal_confidence(location_payload)
        out.append(
            {
                "platform": platform,
                "post_id": post_id,
                "source_event_id": f"{platform}:{post_id}" if platform and post_id else None,
                "created_at": created_at,
                "ingested_at": _parse_date(p.get("ingested_at")),
                "text": _clean_text(p.get("text")),
                "language": _clean_text(p.get("language")),
                "url": _clean_text(p.get("url")),
                "author_id_hash": _clean_text(p.get("author_id_hash")),
                "matched_keywords": matched,
                "excluded_keywords": excluded,
                "flood_relevance_score": relevance,
                "location_confidence": location_confidence,
                "signal_confidence": signal_confidence,
                "place_name": place_name,
                "country": country,
                "latitude": lat,
                "longitude": lon,
                "h3_index": _h3_for(lat, lon),
                "raw_payload": p.get("raw_payload") or p,
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
        country, river_basin, latitude, longitude, geometry,
        deaths, displaced, affected,
        severity, flood_impact_index,
        glide_number, url, h3_index, loaded_at
    ) VALUES (
        :source, :source_event_id, :event_name, :main_cause,
        :date_start, :date_end,
        :country, :river_basin, :latitude, :longitude,
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
        river_basin        = EXCLUDED.river_basin,
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

SOCIAL_SIGNAL_UPSERT_SQL = text(
    """
    INSERT INTO staging.social_flood_signals (
        platform, post_id, source_event_id, created_at, ingested_at,
        text, language, url, author_id_hash,
        matched_keywords, excluded_keywords,
        flood_relevance_score, location_confidence, signal_confidence,
        place_name, country, latitude, longitude, geometry, h3_index,
        raw_payload, loaded_at
    ) VALUES (
        :platform, :post_id, :source_event_id, :created_at, :ingested_at,
        :text, :language, :url, :author_id_hash,
        :matched_keywords, :excluded_keywords,
        :flood_relevance_score, :location_confidence, :signal_confidence,
        :place_name, :country, :latitude, :longitude,
        CASE
            WHEN :latitude IS NOT NULL AND :longitude IS NOT NULL
            THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
            ELSE NULL
        END,
        :h3_index, CAST(:raw_payload AS JSONB), NOW()
    )
    ON CONFLICT (platform, post_id) DO UPDATE SET
        source_event_id       = EXCLUDED.source_event_id,
        created_at            = EXCLUDED.created_at,
        ingested_at           = EXCLUDED.ingested_at,
        text                  = EXCLUDED.text,
        language              = EXCLUDED.language,
        url                   = EXCLUDED.url,
        author_id_hash        = EXCLUDED.author_id_hash,
        matched_keywords      = EXCLUDED.matched_keywords,
        excluded_keywords     = EXCLUDED.excluded_keywords,
        flood_relevance_score = EXCLUDED.flood_relevance_score,
        location_confidence   = EXCLUDED.location_confidence,
        signal_confidence     = EXCLUDED.signal_confidence,
        place_name            = EXCLUDED.place_name,
        country               = EXCLUDED.country,
        latitude              = EXCLUDED.latitude,
        longitude             = EXCLUDED.longitude,
        geometry              = EXCLUDED.geometry,
        h3_index              = EXCLUDED.h3_index,
        raw_payload           = EXCLUDED.raw_payload,
        loaded_at             = NOW();
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


SOCIAL_SIGNAL_DELETE_SQL = text(
    """
    DELETE FROM staging.social_flood_signals
    WHERE platform = :platform AND post_id = :post_id
    """
)


def _upsert_social_signals(rows: list[dict]) -> int:
    """Upsert normalized social flood signals, and proactively delete any
    rows that previously made it into staging but now match a tightened
    exclusion rule.

    Without the delete pass, a row admitted under loose rules would linger in
    ``staging.social_flood_signals`` forever after the filter is tightened.
    The delete is keyed on ``(platform, post_id)`` and only fires for rows
    that have non-empty ``excluded_keywords``, so it never touches signals
    that simply failed an unrelated validity check.
    """
    valid = [
        {
            **r,
            "raw_payload": json.dumps(r.get("raw_payload") or {}, default=str, allow_nan=False),
        }
        for r in rows
        if r.get("platform")
        and r.get("post_id")
        and r.get("created_at")
        and r.get("matched_keywords")
        and not r.get("excluded_keywords")
        and r.get("flood_relevance_score", 0) > 0
        and r.get("signal_confidence", 0) > 0
    ]
    newly_excluded = [
        {"platform": r["platform"], "post_id": r["post_id"]}
        for r in rows
        if r.get("platform")
        and r.get("post_id")
        and r.get("excluded_keywords")
    ]
    chunk = 50
    inserted = 0
    for i in range(0, len(valid), chunk):
        slice_ = valid[i : i + chunk]
        execute_with_retry(SOCIAL_SIGNAL_UPSERT_SQL, slice_)
        inserted += len(slice_)
    deleted = 0
    if newly_excluded:
        for i in range(0, len(newly_excluded), chunk):
            slice_ = newly_excluded[i : i + chunk]
            execute_with_retry(SOCIAL_SIGNAL_DELETE_SQL, slice_)
            deleted += len(slice_)
        logger.info(
            "Removed %s newly-excluded rows from staging.social_flood_signals",
            deleted,
        )
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


def transform_social_media_posts() -> int:
    """Transform raw social posts into staging.social_flood_signals."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT payload, ingested_at FROM raw.social_media_posts")
        )
        payloads = []
        for row in result:
            payload = _as_dict(row[0])
            payload.setdefault("ingested_at", row[1])
            payloads.append(payload)
    if not payloads:
        logger.info("raw.social_media_posts is empty - skipping")
        return 0
    rows = _normalize_social_media_posts(payloads)
    n = _upsert_social_signals(rows)
    logger.info(
        "Transformed raw.social_media_posts -> staging.social_flood_signals: "
        "%s rows upserted",
        n,
    )
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
    try:
        results["social_media_posts"] = transform_social_media_posts()
    except Exception:  # noqa: BLE001
        logger.exception("Transformation failed for raw.social_media_posts")
        results["social_media_posts"] = -1
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_all())
