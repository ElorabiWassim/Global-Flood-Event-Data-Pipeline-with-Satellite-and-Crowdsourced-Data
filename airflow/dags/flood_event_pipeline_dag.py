"""
Airflow DAG: ``flood_event_pipeline``.

End-to-end pipeline for the Global Flood Event Data project:

    apply_schema  →  validate_endpoints
                  →  ingest_<source>  (5 in parallel)
                  →  transform_raw_to_staging
                  →  refresh_marts
                  →  data_quality

The DAG is scheduled daily but is fully manually-triggerable from the
Airflow UI (``catchup=False`` and no ``start_date`` in the future).

Dependencies on the project source code (``scripts/...``) are imported
lazily inside each task to keep DAG-parsing fast and avoid surfacing
heavy library imports (geopandas, h3, etc.) during the scheduler loop.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# Ensure the repo root is on sys.path inside the Airflow container.
# When mounted at /opt/airflow, the project code lives under /opt/airflow/scripts.
_REPO_ROOT = Path(os.getenv("FLOOD_PIPELINE_ROOT", "/opt/airflow"))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------
def _apply_schema(**_) -> None:
    from scripts.db import apply_schema_sql

    apply_schema_sql()


def _validate_endpoints(**_) -> None:
    """Light-weight liveness check for the public endpoints we know about.

    A failure here does NOT stop the DAG; ingestion modules have their own
    fallbacks. The check produces a log line for operators.
    """
    import requests

    urls = {
        "Dartmouth_FO": "https://floodobservatory.colorado.edu/Archives/",
        "ReliefWeb": "https://api.reliefweb.int/v1/disasters?limit=1",
        "Copernicus_EMS": "https://emergency.copernicus.eu/mapping/list-of-activations-rapid",
    }
    for name, url in urls.items():
        try:
            r = requests.head(url, timeout=15, allow_redirects=True)
            logger.info("[endpoint] %s -> HTTP %s", name, r.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[endpoint] %s unreachable: %s", name, exc)


def _ingest_dartmouth(**_) -> None:
    from scripts.ingestion.ingest_dartmouth import run

    run()


def _ingest_glofas(**_) -> None:
    from scripts.ingestion.ingest_glofas import run

    run()


def _ingest_copernicus_ems(**_) -> None:
    from scripts.ingestion.ingest_copernicus_ems import run

    run()


def _ingest_emdat(**_) -> None:
    from scripts.ingestion.ingest_emdat import run

    run()


def _ingest_reliefweb(**_) -> None:
    from scripts.ingestion.ingest_reliefweb import run

    run()


def _transform(**_) -> None:
    from scripts.transform import run_all

    results = run_all()
    logger.info("Transform results: %s", results)


def _refresh_marts(**_) -> None:
    from scripts.marts import refresh_marts

    refresh_marts()


def _data_quality(**_) -> None:
    from scripts.data_quality import run

    result = run()
    logger.info("DQ checks: %s", result["checks"])


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


with DAG(
    dag_id="flood_event_pipeline",
    description="Aggregate global flood events from satellite + crowdsourced sources",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["floods", "supabase", "h3", "postgis"],
) as dag:

    apply_schema = PythonOperator(
        task_id="apply_schema",
        python_callable=_apply_schema,
    )

    validate_endpoints = PythonOperator(
        task_id="validate_endpoints",
        python_callable=_validate_endpoints,
    )

    ingest_dartmouth = PythonOperator(
        task_id="ingest_dartmouth",
        python_callable=_ingest_dartmouth,
    )
    ingest_glofas = PythonOperator(
        task_id="ingest_glofas",
        python_callable=_ingest_glofas,
    )
    ingest_copernicus_ems = PythonOperator(
        task_id="ingest_copernicus_ems",
        python_callable=_ingest_copernicus_ems,
    )
    ingest_emdat = PythonOperator(
        task_id="ingest_emdat",
        python_callable=_ingest_emdat,
    )
    ingest_reliefweb = PythonOperator(
        task_id="ingest_reliefweb",
        python_callable=_ingest_reliefweb,
    )

    transform_raw_to_staging = PythonOperator(
        task_id="transform_raw_to_staging",
        python_callable=_transform,
    )

    refresh_marts = PythonOperator(
        task_id="refresh_marts",
        python_callable=_refresh_marts,
    )

    data_quality = PythonOperator(
        task_id="data_quality",
        python_callable=_data_quality,
        # Always run DQ even if some ingestions failed — we want the report.
        trigger_rule="all_done",
    )

    # ---- Wiring --------------------------------------------------------
    apply_schema >> validate_endpoints

    ingest_tasks = [
        ingest_dartmouth,
        ingest_glofas,
        ingest_copernicus_ems,
        ingest_emdat,
        ingest_reliefweb,
    ]
    for t in ingest_tasks:
        validate_endpoints >> t >> transform_raw_to_staging

    transform_raw_to_staging >> refresh_marts >> data_quality
