"""
FastAPI service exposing the flood-event marts.

The API ONLY queries the ``marts`` schema. If a marts object does not exist
yet (because the pipeline hasn't run) the endpoints return a 503 with a
clear message rather than failing silently.

Run locally:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

# Make project-root imports work even when uvicorn launches from inside api/.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.client import get_engine  # noqa: E402

app = FastAPI(
    title="Global Flood Event API",
    version="1.0.0",
    description=(
        "Query unified flood-event data sourced from Dartmouth FO, GloFAS, "
        "Copernicus EMS, EM-DAT and ReliefWeb."
    ),
)

# Permissive CORS — the bundled dashboard is same-origin, but enabling CORS
# lets external front-ends and notebooks hit the API too. Lock allow_origins
# down before any non-local deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static dashboard (index.html + assets). Mounted under /static so the
# interactive single-page app can be served directly by FastAPI without
# needing a separate web server.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Mart objects that must exist before each route can serve real data.
#
# `events` points at the deduplicated view so analytical endpoints don't
# double-count the DFO Register# overlap between Dartmouth_MasterList (live)
# and Dartmouth_FO (HDX-frozen). The raw vintage-preserving projection
# `marts.flood_events` is still queryable directly via SQL for auditing
# but is intentionally not exposed through the API.
_MART_VIEWS = {
    "events":                    "marts.flood_events_unique",
    "events_with_social":        "marts.flood_events_with_social_signals",
    "social_signals":            "marts.social_flood_signals",
    "social_by_country_day":     "marts.social_signals_by_country_day",
    "by_region":                 "marts.flood_events_by_region",
    "by_h3":                     "marts.flood_events_by_h3",
    "by_basin":                  "marts.flood_frequency_by_basin",
    "by_month":                  "marts.flood_events_by_month",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mart_exists(qualified_name: str) -> bool:
    schema, name = qualified_name.split(".", 1)
    sql = text(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :name
        UNION ALL
        SELECT 1
        FROM information_schema.views
        WHERE table_schema = :schema AND table_name = :name
        LIMIT 1
        """
    )
    try:
        with get_engine().connect() as conn:
            return conn.execute(sql, {"schema": schema, "name": name}).first() is not None
    except Exception:
        return False


def _query(sql: str, **params: Any) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).all()
    return [dict(r._mapping) for r in rows]


def _ensure_or_503(qualified_name: str) -> None:
    if not _mart_exists(qualified_name):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Mart {qualified_name} is not available yet. "
                "Run the Airflow DAG `flood_event_pipeline` to build the marts."
            ),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the interactive dashboard SPA at the root URL.

    Falls back to the JSON service-info payload if `api/static/index.html`
    is missing (e.g. running from a checkout without the static assets).
    """
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    return JSONResponse(_service_info())


def _service_info() -> dict[str, Any]:
    return {
        "service": "Global Flood Event API",
        "version": app.version,
        "dashboard": "/",
        "docs": "/docs",
        "endpoints": [
            "/health",
            "/flood-events",
            "/flood-events/by-region",
            "/flood-events/by-time",
            "/flood-events/by-severity",
            "/flood-events/by-h3",
            "/flood-events/with-social-signals",
            "/social-signals",
            "/analytics/frequency-by-basin",
            "/analytics/by-month",
            "/analytics/by-source",
            "/analytics/social-signals/by-platform",
            "/analytics/social-signals/by-country-day",
        ],
    }


@app.get("/api", tags=["meta"])
def root() -> dict[str, Any]:
    """Machine-readable service info (was previously served at `/`)."""
    return _service_info()


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    """Cheap DB-roundtrip check + mart availability summary."""
    db_ok = False
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        return {"status": "down", "db": str(exc)}
    return {
        "status": "ok",
        "db": db_ok,
        "marts": {key: _mart_exists(name) for key, name in _MART_VIEWS.items()},
    }


@app.get("/flood-events", tags=["events"])
def flood_events(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    source: Optional[str] = None,
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["events"])
    where = "WHERE source = :source" if source else ""
    sql = f"""
        SELECT *
        FROM {_MART_VIEWS["events"]}
        {where}
        ORDER BY date_start DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    return _query(sql, source=source, limit=limit, offset=offset)


@app.get("/flood-events/with-social-signals", tags=["events"])
def flood_events_with_social_signals(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    country: Optional[str] = None,
    min_social_signals: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["events_with_social"])
    where = """
        WHERE (:country IS NULL OR country ILIKE :country)
          AND social_signal_count >= :min_social_signals
    """
    sql = f"""
        SELECT *
        FROM {_MART_VIEWS["events_with_social"]}
        {where}
        ORDER BY social_signal_count DESC, date_start DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    return _query(
        sql,
        country=country,
        min_social_signals=min_social_signals,
        limit=limit,
        offset=offset,
    )


@app.get("/social-signals", tags=["social"])
def social_signals(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    platform: Optional[str] = None,
    country: Optional[str] = None,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["social_signals"])
    sql = f"""
        SELECT *
        FROM {_MART_VIEWS["social_signals"]}
        WHERE (:platform IS NULL OR platform = :platform)
          AND (:country IS NULL OR country ILIKE :country)
          AND COALESCE(signal_confidence, 0) >= :min_confidence
        ORDER BY created_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    return _query(
        sql,
        platform=platform,
        country=country,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


@app.get("/flood-events/by-region", tags=["events"])
def by_region(
    country: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["by_region"])
    where = "WHERE country ILIKE :country" if country else ""
    sql = f"""
        SELECT *
        FROM {_MART_VIEWS["by_region"]}
        {where}
        ORDER BY event_count DESC
        LIMIT :limit
    """
    return _query(sql, country=country, limit=limit)


@app.get("/flood-events/by-time", tags=["events"])
def by_time(
    start: Optional[datetime] = Query(None, description="ISO date-time inclusive"),
    end: Optional[datetime] = Query(None, description="ISO date-time inclusive"),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["events"])
    sql = f"""
        SELECT * FROM {_MART_VIEWS["events"]}
        WHERE (:start IS NULL OR date_start >= :start)
          AND (:end   IS NULL OR date_start <= :end)
        ORDER BY date_start DESC
        LIMIT :limit
    """
    return _query(sql, start=start, end=end, limit=limit)


@app.get("/flood-events/by-severity", tags=["events"])
def by_severity(
    min_severity: float = Query(0.0, ge=0.0),
    max_severity: float = Query(10.0, ge=0.0),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["events"])
    if min_severity > max_severity:
        raise HTTPException(400, "min_severity must be <= max_severity")
    sql = f"""
        SELECT * FROM {_MART_VIEWS["events"]}
        WHERE severity BETWEEN :lo AND :hi
        ORDER BY severity DESC NULLS LAST
        LIMIT :limit
    """
    return _query(sql, lo=min_severity, hi=max_severity, limit=limit)


@app.get("/flood-events/by-h3", tags=["events"])
def by_h3(
    h3_index: str = Query(..., min_length=3, description="H3 cell index"),
) -> dict[str, Any]:
    _ensure_or_503(_MART_VIEWS["by_h3"])
    summary = _query(
        f"SELECT * FROM {_MART_VIEWS['by_h3']} WHERE h3_index = :h",
        h=h3_index,
    )
    if not summary:
        return {"h3_index": h3_index, "event_count": 0, "events": []}
    events = _query(
        f"SELECT * FROM {_MART_VIEWS['events']} WHERE h3_index = :h "
        "ORDER BY date_start DESC LIMIT 200",
        h=h3_index,
    )
    return {**summary[0], "events": events}


@app.get("/analytics/frequency-by-basin", tags=["analytics"])
def frequency_by_basin(
    basin: Optional[str] = None,
    year_min: Optional[int] = Query(None, ge=1900, le=2100),
    year_max: Optional[int] = Query(None, ge=1900, le=2100),
) -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["by_basin"])
    sql = f"""
        SELECT * FROM {_MART_VIEWS["by_basin"]}
        WHERE (:basin IS NULL OR basin ILIKE :basin)
          AND (:y_min IS NULL OR year >= :y_min)
          AND (:y_max IS NULL OR year <= :y_max)
        ORDER BY basin, year
    """
    return _query(sql, basin=basin, y_min=year_min, y_max=year_max)


@app.get("/analytics/by-month", tags=["analytics"])
def by_month() -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["by_month"])
    return _query(f"SELECT * FROM {_MART_VIEWS['by_month']} ORDER BY month")


@app.get("/analytics/by-source", tags=["analytics"])
def by_source() -> list[dict[str, Any]]:
    """Per-source rollup of event counts and impact totals.

    One row per source, served straight from `marts.flood_events`. Lets the
    dashboard render correct totals (and the source-breakdown doughnut) in a
    single round-trip instead of paginating per source.
    """
    _ensure_or_503(_MART_VIEWS["events"])
    sql = f"""
        SELECT source,
               COUNT(*)::bigint                    AS event_count,
               COALESCE(SUM(deaths),    0)::bigint AS total_deaths,
               COALESCE(SUM(displaced), 0)::bigint AS total_displaced,
               MIN(date_start)                     AS earliest_event,
               MAX(date_start)                     AS latest_event
        FROM {_MART_VIEWS["events"]}
        GROUP BY source
        ORDER BY event_count DESC
    """
    return _query(sql)


@app.get("/analytics/social-signals/by-platform", tags=["analytics"])
def social_signals_by_platform() -> list[dict[str, Any]]:
    _ensure_or_503(_MART_VIEWS["social_signals"])
    sql = f"""
        SELECT
            platform,
            COUNT(*)::bigint AS signal_count,
            COUNT(*) FILTER (WHERE country IS NOT NULL)::bigint AS with_country,
            COUNT(*) FILTER (WHERE h3_index IS NOT NULL)::bigint AS with_h3,
            AVG(signal_confidence) AS avg_signal_confidence,
            MIN(created_at) AS earliest_signal,
            MAX(created_at) AS latest_signal
        FROM {_MART_VIEWS["social_signals"]}
        GROUP BY platform
        ORDER BY signal_count DESC
    """
    return _query(sql)


@app.get("/analytics/social-signals/by-country-day", tags=["analytics"])
def social_signals_by_country_day(
    country: Optional[str] = None,
    start: Optional[datetime] = Query(None, description="ISO date inclusive"),
    end: Optional[datetime] = Query(None, description="ISO date inclusive"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Per-day, per-country rollup of social flood signals.

    Useful for monitoring bursts of public flood chatter that operators may
    want to cross-check against authoritative event sources.
    """
    _ensure_or_503(_MART_VIEWS["social_by_country_day"])
    sql = f"""
        SELECT *
        FROM {_MART_VIEWS["social_by_country_day"]}
        WHERE (:country IS NULL OR country ILIKE :country)
          AND (:start   IS NULL OR signal_date >= :start)
          AND (:end     IS NULL OR signal_date <= :end)
          AND COALESCE(avg_signal_confidence, 0) >= :min_confidence
        ORDER BY signal_date DESC, signal_count DESC
        LIMIT :limit
    """
    return _query(
        sql,
        country=country,
        start=start,
        end=end,
        min_confidence=min_confidence,
        limit=limit,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
