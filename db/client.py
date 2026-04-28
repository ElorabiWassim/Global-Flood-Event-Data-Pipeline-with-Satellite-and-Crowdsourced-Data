"""
Shared database utilities.

Exposes a single SQLAlchemy engine plus a few helpers for the rest of the
pipeline so we never have to duplicate connection / DDL boilerplate.
"""

from __future__ import annotations

import json
import logging
import math
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from config.settings import DATABASE_URL, PROJECT_ROOT

logger = logging.getLogger(__name__)

# Detect SQLAlchemy major version so we can opt into 2.0-only kwargs without
# breaking on the 1.4 install pinned by Airflow 2.9.x.
_SA_MAJOR = int(sqlalchemy.__version__.split(".", 1)[0])

# Single engine for the whole process. pool_pre_ping avoids stale connections
# when running through the Supabase pooler.
_engine: Engine | None = None


def get_engine() -> Engine:
    """Singleton SQLAlchemy engine. Uses ``DATABASE_URL`` from .env.

    Tuned for Supabase's pgBouncer pooler:
    - ``pool_pre_ping`` detects dropped connections before queries.
    - ``pool_recycle`` proactively rotates connections older than 5 min.
    - ``insertmanyvalues_page_size`` collapses parameter sets into multi-row
      VALUES clauses (SQLAlchemy 2.0+ only; skipped under SA 1.4 inside the
      Airflow container, which pins SA to 1.4.x).
    - TCP keepalives keep the pooler from killing seemingly-idle sessions.
    """
    global _engine
    if _engine is None:
        kwargs: dict = dict(
            pool_pre_ping=True,
            pool_recycle=300,
            future=True,
            connect_args={
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
                "connect_timeout": 30,
            },
        )
        if _SA_MAJOR >= 2:
            kwargs["insertmanyvalues_page_size"] = 100
        _engine = create_engine(DATABASE_URL, **kwargs)
        logger.debug("SQLAlchemy engine initialized (SA %s).", sqlalchemy.__version__)
    return _engine


@contextmanager
def get_connection() -> Iterator:
    """Yield a connection inside an explicit transaction (auto-commit on exit)."""
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------
def apply_schema_sql(schema_path: Path | None = None) -> None:
    """Apply the canonical schema.sql against the database (idempotent)."""
    # ``schema.sql`` lives next to this module: db/client.py + db/schema.sql.
    schema_path = schema_path or (Path(__file__).with_name("schema.sql"))
    sql = schema_path.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.execute(text(sql))
    logger.info("Applied schema from %s", schema_path)


# ---------------------------------------------------------------------------
# Raw load helpers
# ---------------------------------------------------------------------------
def _clean_for_json(value: Any) -> Any:
    """Recursively replace NaN / +-inf with None so PostgreSQL JSON validates.

    Postgres JSONB strictly follows RFC 8259 and rejects ``NaN`` / ``Infinity``
    tokens, but pandas / numpy frequently produce them for empty cells.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _clean_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_for_json(v) for v in value]
    return value


def insert_raw_records(
    table: str,
    records: Sequence[dict],
    *,
    source: str,
    source_url: str | None,
    file_path: str | None,
    batch_id: str,
) -> int:
    """Insert each ``records[i]`` as a JSONB payload into ``raw.<table>``.

    Returns the number of rows inserted. Records are inserted in chunks to
    avoid round-tripping the wire one row at a time on large CSVs.
    """
    if not records:
        return 0

    rows = [
        {
            "source": source,
            "source_url": source_url,
            "file_path": file_path,
            "batch_id": batch_id,
            "payload": json.dumps(
                _clean_for_json(record),
                default=str,
                allow_nan=False,
            ),
        }
        for record in records
    ]

    sql = text(
        f"""
        INSERT INTO raw.{table}
            (source, source_url, file_path, batch_id, payload)
        VALUES (:source, :source_url, :file_path, :batch_id, CAST(:payload AS JSONB))
        """
    )
    # Smaller batches keep us well under PgBouncer / Supabase pooler limits
    # and let the audit log show progress for very large dumps.
    chunk = 50
    inserted = 0
    for i in range(0, len(rows), chunk):
        slice_ = rows[i : i + chunk]
        execute_with_retry(sql, slice_)
        inserted += len(slice_)
        if (i // chunk) % 20 == 0 and i > 0:
            logger.info(
                "  raw.%s — %s / %s rows inserted...", table, inserted, len(rows)
            )
    logger.info("Inserted %s rows into raw.%s (batch=%s)", inserted, table, batch_id)
    return inserted


def execute_with_retry(sql, params, *, attempts: int = 4) -> None:
    """Execute one chunk with exponential backoff on transient pooler drops."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with get_engine().begin() as conn:
                conn.execute(sql, params)
            return
        except OperationalError as exc:
            last_exc = exc
            wait = 2 ** (attempt - 1)
            logger.warning(
                "  pooler dropped connection (attempt %s/%s): retrying in %ss",
                attempt,
                attempts,
                wait,
            )
            # Force the engine to drop its pool so the next attempt opens
            # a fresh socket.
            _reset_engine()
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _reset_engine() -> None:
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        _engine = None


def log_ingestion(
    *,
    batch_id: str,
    source: str,
    status: str,
    rows_ingested: int = 0,
    source_url: str | None = None,
    file_path: str | None = None,
    file_checksum: str | None = None,
    message: str | None = None,
    started_at: datetime | None = None,
) -> None:
    """Persist an audit row in raw.ingestion_log."""
    sql = text(
        """
        INSERT INTO raw.ingestion_log
            (batch_id, source, source_url, file_path, file_checksum,
             rows_ingested, status, message, started_at, finished_at)
        VALUES
            (:batch_id, :source, :source_url, :file_path, :file_checksum,
             :rows_ingested, :status, :message, :started_at, :finished_at)
        """
    )
    now = datetime.now(timezone.utc)
    try:
        with get_connection() as conn:
            conn.execute(
                sql,
                {
                    "batch_id": batch_id,
                    "source": source,
                    "source_url": source_url,
                    "file_path": file_path,
                    "file_checksum": file_checksum,
                    "rows_ingested": rows_ingested,
                    "status": status,
                    "message": message,
                    "started_at": started_at or now,
                    "finished_at": now,
                },
            )
    except SQLAlchemyError as exc:
        # Audit failures must never crash the pipeline.
        logger.warning("Failed to write ingestion_log row: %s", exc)


def truncate_raw_table(table: str) -> None:
    """Empty a ``raw.<table>`` before reloading. Useful for full refresh runs."""
    with get_connection() as conn:
        conn.execute(text(f"TRUNCATE TABLE raw.{table} RESTART IDENTITY"))
    logger.info("Truncated raw.%s", table)


def fetch_all(sql: str, **params) -> list[dict]:
    """Execute a SELECT and return rows as plain dicts."""
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(row._mapping) for row in result]
