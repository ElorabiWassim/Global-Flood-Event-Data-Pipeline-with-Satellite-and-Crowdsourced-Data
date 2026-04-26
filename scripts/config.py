"""
Central configuration for the flood event pipeline.

All credentials and tunables come from environment variables (loaded from .env
when running locally; injected via docker-compose / Airflow connections in
production). Nothing is hard-coded.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Repository root is two levels up from this file: <root>/scripts/config.py
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

# .env lives at the project root. load_dotenv silently no-ops if missing.
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR: Path = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR: Path = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"
LOGS_DIR: Path = DATA_DIR / "logs"

for _p in (RAW_DIR, PROCESSED_DIR, LOGS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_database_url() -> str:
    """Return the SQLAlchemy connection string.

    Prefers DATABASE_URL; otherwise builds it from POSTGRES_* parts so the
    same code works for Supabase pooler URLs and local Postgres.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    user = os.getenv("POSTGRES_USER", "postgres")
    pwd = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"


DATABASE_URL: str = get_database_url()

# ---------------------------------------------------------------------------
# Pipeline tunables
# ---------------------------------------------------------------------------
H3_RESOLUTION: int = int(os.getenv("H3_RESOLUTION", "7"))
HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "60"))
RELIEFWEB_APPNAME: str = os.getenv("RELIEFWEB_APPNAME", "flood-event-pipeline")

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))
