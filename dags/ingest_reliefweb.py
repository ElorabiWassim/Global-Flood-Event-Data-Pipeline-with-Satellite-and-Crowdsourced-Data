from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from ingestion.reliefweb import main as ingest_reliefweb_main


# ── Daily incremental DAG (runs automatically every day) ───────────────────
with DAG(
    dag_id="reliefweb_flood_ingestion",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["reliefweb", "floods"],
) as dag:

    PythonOperator(
        task_id="ingest_reliefweb_flood_events",
        python_callable=ingest_reliefweb_main,
        op_kwargs={"mode": "incremental"},
    )


# ── Full backfill DAG (manual trigger only — to seed all history) ──────────
with DAG(
    dag_id="reliefweb_flood_ingestion_full",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["reliefweb", "floods", "backfill"],
) as dag_full:

    PythonOperator(
        task_id="ingest_reliefweb_flood_events_full",
        python_callable=ingest_reliefweb_main,
        op_kwargs={"mode": "full"},
    )