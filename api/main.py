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

# Mart objects that must exist before each route can serve real data.
_MART_VIEWS = {
    "events":     "marts.flood_events",
    "by_region":  "marts.flood_events_by_region",
    "by_h3":      "marts.flood_events_by_h3",
    "by_basin":   "marts.flood_frequency_by_basin",
    "by_month":   "marts.flood_events_by_month",
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
@app.get("/", tags=["meta"])
def root() -> dict[str, Any]:
    return {
        "service": "Global Flood Event API",
        "version": app.version,
        "endpoints": [
            "/health",
            "/flood-events",
            "/flood-events/by-region",
            "/flood-events/by-time",
            "/flood-events/by-severity",
            "/flood-events/by-h3",
            "/analytics/frequency-by-basin",
        ],
    }


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
