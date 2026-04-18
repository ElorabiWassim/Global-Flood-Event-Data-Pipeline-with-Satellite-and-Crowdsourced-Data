from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from ingestion.emdat import main as ingest_emdat_main


with DAG(
    dag_id="emdat_flood_ingestion",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["emdat", "floods"],
) as dag:
    ingest_emdat = PythonOperator(
        task_id="ingest_emdat_flood_events",
        python_callable=ingest_emdat_main,
    )
