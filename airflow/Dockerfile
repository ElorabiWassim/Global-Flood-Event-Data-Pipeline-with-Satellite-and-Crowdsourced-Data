# ----------------------------------------------------------------------------
# Custom Airflow image for the Global Flood Event Data Pipeline.
#
# Why a custom image instead of _PIP_ADDITIONAL_REQUIREMENTS?
#   * pip install runs at every container start, adding ~60s+ to bring-up.
#   * dbt-core conflicts with Airflow's pinned versions of jinja2 / click
#     unless installed at build time, in the right order.
#   * Reproducible builds: image digest is the only moving part.
#
# Why --constraint?
#   apache/airflow:2.9.3 is built against SQLAlchemy 1.4.x. Installing a
#   newer SA (2.0+) on top breaks Airflow's ORM with the well-known
#   "TaskInstance.dag_model annotation" error. Airflow's published
#   constraints file pins SA (and ~600 other deps) to a known-good set, so
#   we use it for every pip install in this image. Packages that are NOT
#   in the constraint file (h3, dbt-core, dbt-postgres) install normally.
#
# Build:
#   docker compose build airflow
# ----------------------------------------------------------------------------
FROM apache/airflow:2.9.3-python3.11

ARG AIRFLOW_VERSION=2.9.3
ARG PYTHON_VERSION=3.11
ARG CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# ---- System libs -----------------------------------------------------------
# psycopg2-binary ships its own libpq, but dbt-postgres still wants libpq for
# its psycopg2 adapter. Install as root, then drop back to the airflow user.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER airflow

# ---- Pipeline runtime deps -------------------------------------------------
# pandas / numpy / SQLAlchemy / psycopg2 are all in Airflow's constraints, so
# we let the constraint file pick the version that matches Airflow 2.9.3.
RUN pip install --no-cache-dir --constraint "${CONSTRAINT_URL}" \
        pandas \
        numpy \
        python-dotenv \
        requests \
        openpyxl \
        SQLAlchemy \
        psycopg2-binary

# h3 + pyshp are NOT in Airflow's constraints, so we pin them explicitly.
# Both ship pure-Python wheels and have no native deps.
#   * h3 3.7.x — H3 indexing in the staging transformer
#   * pyshp 2.3.x — reads the Dartmouth FO HDX shapefile (.dbf attributes)
RUN pip install --no-cache-dir h3==3.7.7 pyshp==2.3.1

# dbt has its own tight pin set (jinja2, click, agate, mashumaro, packaging,
# protobuf, ...) that conflicts with Airflow's constraints file. We install
# dbt WITHOUT the constraint because:
#   1. dbt is invoked as a CLI subprocess by Airflow's BashOperator, not
#      imported into Airflow's Python — so it does not need to share
#      Airflow's exact dep versions at import time.
#   2. dbt-core ships its own internally-consistent dep tree; pip can resolve
#      it cleanly when left alone.
# If a future shared dep ends up downgraded in a way Airflow doesn't like,
# move dbt into its own virtualenv (python -m venv /opt/dbt-venv && ...).
RUN pip install --no-cache-dir \
        dbt-core==1.7.13 \
        dbt-postgres==1.7.13
