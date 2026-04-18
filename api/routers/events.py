from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import get_db


router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    source: str = Query(default="emdat"),
    country: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    clauses = ["source = :source"]
    params: dict[str, Any] = {"source": source, "limit": limit}

    if country:
        clauses.append("country = :country")
        params["country"] = country

    if start_date:
        clauses.append("date_start >= :start_date")
        params["start_date"] = start_date

    if end_date:
        clauses.append("date_start <= :end_date")
        params["end_date"] = end_date

    sql = text(
        f"""
        select
            id,
            source,
            source_event_id,
            event_name,
            main_cause,
            date_start,
            date_end,
            country,
            latitude,
            longitude,
            deaths,
            displaced,
            affected,
            severity,
            flood_impact_index,
            glide_number,
            url,
            h3_index,
            river_basin
        from flood_events
        where {' and '.join(clauses)}
        order by date_start desc
        limit :limit
        """
    )

    result = db.execute(sql, params)
    return [dict(row._mapping) for row in result]
