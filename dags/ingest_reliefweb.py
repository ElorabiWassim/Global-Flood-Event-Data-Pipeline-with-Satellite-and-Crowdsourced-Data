# dags/ingest_reliefweb.py
"""
Airflow DAG — ReliefWeb daily flood ingestion.

Two DAGs in one file:
  - ingest_reliefweb          → runs every day automatically (incremental)
  - ingest_reliefweb_full     → triggered manually once (full backfill)
"""

from __future__ import annotations
from datetime import datetime, timedelta
from airflow import DAG  # pyright: ignore[reportMissingImports]
from airflow.operators.python import PythonOperator  # pyright: ignore[reportMissingImports]

default_args = {
    "owner":            "flood-team",
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
    "email_on_failure": False,
}


# ── Task: incremental ingest ───────────────────────────────────

def task_ingest_incremental(**context):
    import sys
    sys.path.insert(0, "/opt/airflow")
    from ingestion.reliefweb import main as run

    inserted = run(mode="incremental")
    context["ti"].xcom_push(key="rows_inserted", value=inserted)
    print(f"Task done: {inserted} rows inserted.")


# ── Task: full backfill ────────────────────────────────────────

def task_ingest_full(**context):
    import sys
    sys.path.insert(0, "/opt/airflow")
    from ingestion.reliefweb import main as run

    inserted = run(mode="full")
    context["ti"].xcom_push(key="rows_inserted", value=inserted)
    print(f"Full backfill done: {inserted} rows inserted.")


# ── Task: validate ─────────────────────────────────────────────

def task_validate(**context):
    import sys
    sys.path.insert(0, "/opt/airflow")
    from sqlalchemy import create_engine, text  # pyright: ignore[reportMissingImports]
    import os

    required_env = [
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
    ]
    missing = [name for name in required_env if not os.getenv(name)]
    if missing:
        raise ValueError(
            "Missing required DB environment variables: " + ", ".join(missing)
        )

    db_url = (
        f"postgresql+psycopg2://"
        f"{os.getenv('POSTGRES_USER')}:"
        f"{os.getenv('POSTGRES_PASSWORD')}@"
        f"{os.getenv('POSTGRES_HOST')}:"
        f"{os.getenv('POSTGRES_PORT')}/"
        f"{os.getenv('POSTGRES_DB')}"
    )

    engine = create_engine(db_url)
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM flood_events WHERE source = 'reliefweb'")
        ).scalar()

    if total == 0:
        raise ValueError("Validation failed: 0 ReliefWeb rows in DB after ingestion.")

    rows_inserted = context["ti"].xcom_pull(key="rows_inserted") or 0
    print(f"Validation passed. Total ReliefWeb rows in DB: {total}. New this run: {rows_inserted}.")


# ── DAG 1: Daily incremental (auto) ───────────────────────────

with DAG(
    dag_id            = "ingest_reliefweb",
    description       = "Daily incremental ReliefWeb flood ingestion",
    default_args      = default_args,
    start_date        = datetime(2024, 1, 1),
    schedule_interval = "@daily",          # runs every day at midnight UTC
    catchup           = False,             # don't replay missed days
    tags              = ["flood", "reliefweb", "incremental"],
) as dag_incremental:

    ingest   = PythonOperator(task_id="ingest",    python_callable=task_ingest_incremental, provide_context=True)
    validate = PythonOperator(task_id="validate",  python_callable=task_validate,           provide_context=True)

    ingest >> validate   # validate runs only after ingest succeeds


# ── DAG 2: Full backfill (manual trigger only) ─────────────────

with DAG(
    dag_id            = "ingest_reliefweb_full",
    description       = "One-time full ReliefWeb backfill (trigger manually)",
    default_args      = default_args,
    start_date        = datetime(2024, 1, 1),
    schedule_interval = None,              # never runs automatically
    catchup           = False,
    tags              = ["flood", "reliefweb", "backfill"],
) as dag_full:

    ingest_full   = PythonOperator(task_id="ingest",   python_callable=task_ingest_full, provide_context=True)
    validate_full = PythonOperator(task_id="validate", python_callable=task_validate,    provide_context=True)

    ingest_full >> validate_full
