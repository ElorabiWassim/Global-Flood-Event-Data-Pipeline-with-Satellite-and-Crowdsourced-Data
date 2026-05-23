# Global Flood Event Data Pipeline

An end-to-end, reproducible data pipeline that aggregates historical and
near-real-time flood-event data from **satellite observations**, **government
disaster databases**, **humanitarian news feeds**, and **public social-media
flood signals** into a single, unified, analysis-ready **PostgreSQL / PostGIS +
H3** data warehouse, exposed through a **FastAPI** service and an interactive
dashboard, orchestrated by **Apache Airflow** and modeled with **dbt**.

> **Repository**: <https://github.com/ElorabiWassim/Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data>

---

## Table of Contents

1. [What you get](#1-what-you-get)
2. [Architecture](#2-architecture)
3. [Repository layout](#3-repository-layout)
4. [Prerequisites](#4-prerequisites)
5. [Quick start (Docker, recommended)](#5-quick-start-docker-recommended)
6. [Local development setup (no Docker)](#6-local-development-setup-no-docker)
7. [Configuration reference](#7-configuration-reference)
8. [Running the pipeline](#8-running-the-pipeline)
9. [dbt warehouse layer](#9-dbt-warehouse-layer)
10. [API reference](#10-api-reference)
11. [Testing & data quality](#11-testing--data-quality)
12. [Analytical outputs (notebooks)](#12-analytical-outputs-notebooks)
13. [Troubleshooting](#13-troubleshooting)
14. [License](#15-license)

---

## 1. What you get

| Layer                    | Count | Where                                                                |
| ------------------------ | ----: | -------------------------------------------------------------------- |
| Raw data sources         |     6 | `ingestion/ingest_*.py`                                              |
| Raw tables (`raw.*`)     |     6 | `db/schema.sql`                                                      |
| Staging tables           |     2 | `staging.flood_events`, `staging.social_flood_signals`               |
| Mart views               |     8 | `transformations/marts.py` (+ dbt project in `dbt/`)                 |
| Airflow DAG tasks        |     9 | `dags/flood_event_pipeline_dag.py`                                   |
| REST endpoints           |    15 | `api/main.py`                                                        |
| Data-quality checks      |    15 | `validation/data_quality.py` (7 flood + 8 social)                    |
| Unit tests               |   117 | `tests/`                                                             |
| Source normalizers       |     5 | `transformations/transform.py`                                       |

---

## 2. Architecture

```
                           +--------------------------+
                           |  Apache Airflow (DAG)    |
                           |  flood_event_pipeline    |
                           +--------------------------+
                                       |
   +---------+--------------+--------+-+--------+--------------+----------+
   |         |              |        |          |              |          |
Dartmouth_FO Dartmouth_  Copernicus EMS      EM-DAT       ReliefWeb     Bluesky
(HDX, frozen) MasterList (Rapid Mapping)   (CRED HDX)      API          search API
   |         |              |        |          |              |          |
   v         v              v        v          v              v          v
   data/raw/<source>/  (CSV / XLSX / JSON / SHP snapshots + .meta.json sidecars)
        \____________________  \_____  ____________________/             |
                              \      \/                                  |
                       INSERT into raw.<source>_events                   |
                       raw.social_media_posts <-------------------------/
                                  |
                  transformations/transform.py
                     |                            |
                     v                            v
        staging.flood_events           staging.social_flood_signals
        (canonical, H3 + PostGIS)      (filtered social observations)
                                  |
                  transformations/marts.py   (or:  dbt run)
                                  |
                                  v
              marts.flood_events             marts.flood_events_unique
              marts.flood_events_by_region   marts.flood_events_by_month
              marts.flood_events_by_source   marts.flood_frequency_by_basin
              marts.social_flood_signals     marts.social_signals_by_country_day
              marts.flood_events_with_social_signals
                                  |
                                  v
                          FastAPI + dashboard (api/main.py)
```

### Medallion schemas

| Schema    | Purpose                                                                          |
| --------- | -------------------------------------------------------------------------------- |
| `raw`     | Untouched ingestion. One JSONB row per source row / social post + audit log.     |
| `staging` | Canonical unified `flood_events` table and normalized `social_flood_signals`.    |
| `marts`   | API-ready views built on top of `staging`, including social rollups & joins.     |

Full DDL: [`db/schema.sql`](./db/schema.sql) (applied idempotently by the DAG's
first task).

---

## 3. Repository layout

```
.
├── api/                          # FastAPI service (15 routes)
│   ├── main.py
│   ├── Dockerfile
│   └── static/                   # interactive dashboard assets
├── airflow/                      # Airflow runtime state
│   ├── Dockerfile                #   custom image (pandas, h3, dbt, SQLAlchemy)
│   ├── logs/                     #   .gitignored
│   └── plugins/
├── config/
│   ├── __init__.py
│   └── settings.py               # env-driven configuration
├── dags/
│   └── flood_event_pipeline_dag.py
├── data/
│   ├── raw/<source>/             # ingested files + .meta.json sidecars
│   ├── processed/                # .gitignored
│   └── logs/data_quality_report.md
├── db/
│   ├── __init__.py
│   ├── client.py                 # SQLAlchemy engine + retry helpers
│   └── schema.sql                # canonical PostGIS DDL
├── dbt/                          # dbt project (staging + marts)
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── sources.yml
│       ├── staging/stg_dfo.sql
│       └── marts/flood_events.sql
├── docs/
│   ├── data_sources.md
│   ├── project_status.md
│   ├── project_status.pdf
│   └── social_media_ingestion/
├── ingestion/                    # raw-load layer (one module per source)
│   ├── common.py
│   ├── ingest_bluesky.py
│   ├── ingest_copernicus_ems.py
│   ├── ingest_dartmouth.py
│   ├── ingest_emdat.py
│   ├── ingest_glofas.py
│   └── ingest_reliefweb.py
├── notebooks/
│   └── time_series_analysis.ipynb
├── requirements/
│   ├── base.txt
│   ├── dev.txt
│   └── notebooks.txt
├── scripts/
│   └── _make_status_pdf.py
├── tests/                        # 117 unit tests (pytest)
│   ├── conftest.py
│   ├── test_db_client.py
│   ├── test_ingest_bluesky.py
│   ├── test_ingestion_common.py
│   ├── test_social_geo.py
│   └── test_transform.py
├── transformations/              # raw -> staging -> marts
│   ├── transform.py
│   ├── social_geo.py
│   └── marts.py
├── validation/
│   └── data_quality.py           # 15 automated checks
├── .env.example                  # template — copy to .env
├── .gitignore
├── docker-compose.yml
├── pytest.ini
└── README.md
```

---

## 4. Prerequisites

| Tool                  | Minimum version | Notes                                                |
| --------------------- | --------------- | ---------------------------------------------------- |
| Docker Desktop        | 24.x            | Required for the Docker quick-start path             |
| Docker Compose plugin | v2.x            | Bundled with modern Docker Desktop                   |
| Python                | 3.11            | Required for the local-dev path                      |
| Git                   | 2.30+           |                                                      |
| (Optional) PostgreSQL | 14+ with PostGIS 3.x | Only if you skip the Supabase/managed-DB option |

You also need a PostgreSQL database with the **PostGIS** extension enabled.
Any of the following works:

- A free [Supabase](https://supabase.com) project (PostGIS is pre-installed)
- A local PostgreSQL 14+ with `CREATE EXTENSION postgis;`
- A managed Postgres (RDS, Cloud SQL, Neon, Crunchy) with PostGIS

---

## 5. Quick start (Docker, recommended)

These commands take a clean machine to a running pipeline + API in **under
five minutes** (assuming a reachable Postgres URL).

```bash
# 1) Clone the repository
git clone https://github.com/ElorabiWassim/Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data.git
cd Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data

# 2) Create your local .env from the template, then edit it
cp .env.example .env
#   Windows PowerShell: Copy-Item .env.example .env
#
#   Edit .env and set at minimum:
#     - DATABASE_URL (or POSTGRES_HOST/USER/PASSWORD/DB)
#     - BLUESKY_HANDLE / BLUESKY_APP_PASSWORD   (optional)

# 3) Build and start Airflow + the FastAPI service
docker compose up --build -d

# 4) Watch logs until "airflow standalone | Webserver ... started"
docker compose logs -f airflow

# 5) Open the two web UIs
#    Airflow UI  -> http://localhost:8081     (user: admin, password: admin)
#    FastAPI doc -> http://localhost:8000/docs
#    Dashboard   -> http://localhost:8000/
```

The first start of the `airflow` container will:

- Run `airflow db migrate` to initialize the metadata DB
- Create the `admin` Airflow user idempotently
- Install pipeline dependencies (pandas, h3, dbt, SQLAlchemy, …)
- Launch `airflow standalone` (web + scheduler in one process)

### Trigger your first pipeline run

In the Airflow UI:

1. Locate the **`flood_event_pipeline`** DAG
2. Toggle it **ON**
3. Click the ▶ **Trigger DAG** button

…or from the CLI:

```bash
docker exec -it flood_airflow airflow dags trigger flood_event_pipeline
```

The DAG runs `Schema setup → 6 parallel ingestions → Transform → Build marts
→ DQ check` and finishes by writing
[`data/logs/data_quality_report.md`](./data/logs/data_quality_report.md).

### Smoke-test the API

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/flood-events?limit=5"
curl "http://localhost:8000/flood-events/by-time?start=2020-01-01&end=2020-12-31"
```

### Stopping & cleaning up

```bash
docker compose down                # stop containers, keep volumes
docker compose down -v --rmi local # nuke containers, images, volumes
```

---

## 6. Local development setup (no Docker)

Use this path if you want to iterate on Python code without rebuilding the
image, or if Docker is unavailable.

```bash
# 1) Clone the repo and cd in
git clone https://github.com/ElorabiWassim/Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data.git
cd Global-Flood-Event-Data-Pipeline-with-Satellite-and-Crowdsourced-Data

# 2) Create a Python 3.11 virtualenv
python -m venv .venv

#    Activate it
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\Activate.ps1         # Windows PowerShell

# 3) Install dependencies
pip install --upgrade pip
pip install -r requirements/base.txt
pip install -r requirements/dev.txt          # pytest + tooling

# 4) Configure your environment
cp .env.example .env                          # then edit it

# 5) Apply the canonical schema to your database
python -c "from db.client import apply_schema_sql; apply_schema_sql()"

# 6) Run one or more ingestion modules
python -m ingestion.ingest_dartmouth
python -m ingestion.ingest_reliefweb
python -m ingestion.ingest_emdat
python -m ingestion.ingest_copernicus_ems
python -m ingestion.ingest_glofas             # pulls DFO MasterList
python -m ingestion.ingest_bluesky            # optional

# 7) Normalize raw -> staging
python -m transformations.transform

# 8) Build/refresh marts
python -m transformations.marts

# 9) Run the 15 data-quality checks
python -m validation.data_quality

# 10) Serve the API
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000/docs> for Swagger UI and <http://localhost:8000/>
for the dashboard.

---

## 7. Configuration reference

All credentials and tunables are loaded from `.env` via `python-dotenv` in
[`config/settings.py`](./config/settings.py). Copy [`.env.example`](./.env.example)
to `.env` and adjust:

| Variable                            | Required | Default                              | Purpose                                           |
| ----------------------------------- | :------: | ------------------------------------ | ------------------------------------------------- |
| `DATABASE_URL`                      |    *     | *(built from POSTGRES_*)*            | Full SQLAlchemy URL (preferred).                  |
| `POSTGRES_HOST`                     |    *     | `localhost`                          | Used when `DATABASE_URL` is unset.                |
| `POSTGRES_PORT`                     |    *     | `5432`                               |                                                   |
| `POSTGRES_DB`                       |    *     | `postgres`                           |                                                   |
| `POSTGRES_USER`                     |    *     | `postgres`                           |                                                   |
| `POSTGRES_PASSWORD`                 |    *     | `postgres`                           |                                                   |
| `API_HOST`                          |          | `0.0.0.0`                            | FastAPI bind host                                 |
| `API_PORT`                          |          | `8000`                               | FastAPI bind port                                 |
| `H3_RESOLUTION`                     |          | `7`                                  | H3 cell resolution (≈ 5 km edge length)           |
| `HTTP_TIMEOUT`                      |          | `60`                                 | Per-request timeout (s) for ingestion modules     |
| `RELIEFWEB_APPNAME`                 |          | `ai-students-flood-project-...`      | App identifier sent to the public ReliefWeb API   |
| `EMDAT_DOWNLOAD_URL`                |          | —                                    | Signed URL for EM-DAT bulk export (optional)      |
| `COPERNICUS_EMS_FEED_URL`           |          | —                                    | Override for the bundled `activations.csv`        |
| `CDS_API_URL` / `CDS_API_KEY`       |          | —                                    | Copernicus CDS credentials (future GloFAS branch) |
| `AIRFLOW_UID`                       |          | `50000`                              | UID Airflow processes run as inside the container |
| `ENABLE_SOCIAL_MEDIA_INGESTION`     |          | `true`                               | Toggles the Bluesky task inside the DAG           |
| `BLUESKY_HANDLE`                    |          | —                                    | Optional Bluesky handle for authenticated search  |
| `BLUESKY_APP_PASSWORD`              |          | —                                    | Optional Bluesky App Password                     |
| `BLUESKY_MAX_POSTS`                 |          | `100`                                | Max normalized posts per run                      |
| `BLUESKY_PER_QUERY_LIMIT`           |          | `25`                                 | Per-keyword search page size                      |
| `BLUESKY_LOOKBACK_HOURS`            |          | `24`                                 | Rolling window for scheduled runs                 |

\* At least one of `DATABASE_URL` or the `POSTGRES_*` quartet is required.

> ⚠️ **Never commit `.env`.** The `.gitignore` excludes it by default. If you
> ever pushed a real `.env`, **rotate every credential it contains** — older
> commits keep the secrets even after deletion.

---

## 8. Running the pipeline

### DAG task graph

```
              schema_setup
                  │
   ┌──────┬──────┬┴───────┬──────────┬─────────┐
   ▼      ▼      ▼        ▼          ▼         ▼
dartmouth glofas copernicus_ems emdat reliefweb bluesky        (6 in parallel)
   └──────┴──────┴────┬───┴──────────┴─────────┘
                      ▼
                  transform
                      ▼
                build_marts
                      ▼
                   dq_check        (trigger_rule="all_done")
```

### Manual ad-hoc invocations

```bash
# Replay a single source from its cached raw snapshot:
docker exec -it flood_airflow python -m ingestion.ingest_dartmouth

# Re-build marts only (after editing transformations/marts.py):
docker exec -it flood_airflow python -m transformations.marts

# Run only the DQ task and view the report:
docker exec -it flood_airflow python -m validation.data_quality
docker exec -it flood_airflow cat /opt/airflow/data/logs/data_quality_report.md
```

---

## 9. dbt warehouse layer

The repository includes a parallel **dbt** project that materializes the same
medallion architecture as views, suitable for analytics teams that prefer dbt
to imperative Python transforms.

```bash
# Inside the airflow container (dbt is pre-installed):
docker exec -it flood_airflow bash -lc "cd /opt/airflow/dbt && dbt run --profiles-dir ."
docker exec -it flood_airflow bash -lc "cd /opt/airflow/dbt && dbt test --profiles-dir ."
docker exec -it flood_airflow bash -lc "cd /opt/airflow/dbt && dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir ."
```

Project layout:

```
dbt/
├── dbt_project.yml          # name=flood_pipeline, profile=flood_pipeline
├── profiles.yml             # reads DATABASE_URL from env
└── models/
    ├── sources.yml          # raw.* source definitions
    ├── staging/             # +schema=staging, materialized=view
    │   └── stg_dfo.sql
    └── marts/               # +schema=marts, materialized=view
        └── flood_events.sql
```

> The Python `transformations/marts.py` and the dbt project both write into
> the `marts` schema. In production, pick one as the source of truth; today
> the Airflow DAG calls the Python version for deterministic refreshes.

---

## 10. API reference

The FastAPI service reads **only** from mart views (never raw or staging), so
responses are fast, consistent across endpoints, and degrade safely (HTTP 503
if a mart is missing).

| #  | Method | Path                                            | Tag       |
| -: | ------ | ----------------------------------------------- | --------- |
|  1 | GET    | `/`                                             | dashboard |
|  2 | GET    | `/api`                                          | meta      |
|  3 | GET    | `/health`                                       | meta      |
|  4 | GET    | `/flood-events`                                 | events    |
|  5 | GET    | `/flood-events/with-social-signals`             | events    |
|  6 | GET    | `/social-signals`                               | social    |
|  7 | GET    | `/flood-events/by-region`                       | events    |
|  8 | GET    | `/flood-events/by-time`                         | events    |
|  9 | GET    | `/flood-events/by-severity`                     | events    |
| 10 | GET    | `/flood-events/by-h3`                           | events    |
| 11 | GET    | `/analytics/frequency-by-basin`                 | analytics |
| 12 | GET    | `/analytics/by-month`                           | analytics |
| 13 | GET    | `/analytics/by-source`                          | analytics |
| 14 | GET    | `/analytics/social-signals/by-platform`         | analytics |
| 15 | GET    | `/analytics/social-signals/by-country-day`      | analytics |

Examples:

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/flood-events?limit=5"
curl "http://localhost:8000/flood-events/by-region?country=Vietnam"
curl "http://localhost:8000/flood-events/by-time?start=2020-01-01&end=2020-12-31"
curl "http://localhost:8000/flood-events/by-severity?min_severity=2"
curl "http://localhost:8000/flood-events/by-h3?h3_index=87283472bffffff"
curl "http://localhost:8000/flood-events/with-social-signals?limit=5"
curl "http://localhost:8000/social-signals?limit=5"
curl "http://localhost:8000/analytics/frequency-by-basin?basin=Vietnam"
curl "http://localhost:8000/analytics/by-month"
curl "http://localhost:8000/analytics/by-source"
curl "http://localhost:8000/analytics/social-signals/by-platform"
curl "http://localhost:8000/analytics/social-signals/by-country-day?limit=20"
```

Interactive Swagger UI: <http://localhost:8000/docs>.

---

## 11. Testing & data quality

### Unit tests (pytest)

```bash
# inside .venv, from project root
pip install -r requirements/dev.txt
pytest                              # runs all 117 tests
pytest tests/test_transform.py -v   # one module
pytest -k bluesky                   # by keyword
```

Test inventory (`tests/`):

| File                          | Functions |
| ----------------------------- | --------: |
| `test_db_client.py`           |        16 |
| `test_ingest_bluesky.py`      |        26 |
| `test_ingestion_common.py`    |        10 |
| `test_social_geo.py`          |        11 |
| `test_transform.py`           |        54 |
| **Total**                     |   **117** |

### Data-quality checks (15)

```bash
python -m validation.data_quality
cat data/logs/data_quality_report.md
```

The 15 checks are defined as `(name, SQL)` tuples in
[`validation/data_quality.py`](./validation/data_quality.py):

**Flood-event checks (7):** `duplicate_source_event_ids`,
`missing_date_start`, `invalid_latitude_or_longitude`, `invalid_geometry`,
`missing_source`, `missing_h3_with_coords`, `severity_out_of_range`.

**Social-signal checks (8):** `social_duplicate_platform_post_ids`,
`social_missing_created_at`, `social_missing_platform_or_post_id`,
`social_invalid_latitude_or_longitude`, `social_missing_h3_with_coords`,
`social_confidence_out_of_range`, `social_relevance_without_keywords`,
`social_orphan_staging_signals`.

The DQ task uses `trigger_rule="all_done"` so it runs even if an upstream
ingestion fails — you always get a report.

---

## 12. Analytical outputs (notebooks)

```bash
pip install -r requirements/notebooks.txt
jupyter lab notebooks/time_series_analysis.ipynb
```

`notebooks/time_series_analysis.ipynb` covers:

- Monthly event time-series construction from `marts.flood_events_by_month`
- Seasonal decomposition (STL) per region
- Per-basin frequency trend lines from `marts.flood_frequency_by_basin`
- Cross-source corroboration counts joined with `marts.flood_events_with_social_signals`

---

## 13. Troubleshooting

| Symptom                                                              | Likely cause                                      | Fix                                                                                                            |
| -------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `sqlalchemy.exc.OperationalError: could not connect to server`       | Wrong `DATABASE_URL` or DB not reachable          | Verify `psql "$DATABASE_URL"` works from your shell.                                                           |
| API returns `503 Mart X is not available yet`                        | DAG has not finished                              | Trigger the DAG once or run `python -m transformations.marts` locally.                                         |
| `extension "postgis" is not available`                               | PostGIS not installed in your DB                  | Use Supabase (PostGIS pre-installed) or run `CREATE EXTENSION postgis;` as a superuser.                        |
| Airflow UI shows DAG paused on every restart                         | Default Airflow behaviour                         | Toggle the DAG on, or set `AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=False` in `docker-compose.yml`.          |
| Port 8081 / 8000 already in use                                      | Another process owns the port                     | Edit the `ports:` mapping in `docker-compose.yml`.                                                             |
| `ImportError: cannot import name '...' from 'h3'`                    | h3 version mismatch                               | The pipeline pins `h3==3.7.7` (pure-Python). Re-create the venv with `requirements/base.txt`.                  |
| Bluesky ingestion logs `401 Unauthorized`                            | App Password missing / invalid                    | Create a new App Password at <https://bsky.app/settings/app-passwords> and update `.env`.                      |
| `pre_ping` errors against Supabase                                   | pgBouncer transaction-pool quirks                 | `db/client.py` already sets `pool_pre_ping=True`, `pool_recycle=300s`. If it persists, increase `pool_recycle`. |

---

## 14. License


The source code in this repository is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

Datasets are not included in this repository and remain subject to their original licenses.

---

*Maintainer: Group G7 — Final Year Project Propositions, Academic Year 2025-2026
Elorabi Wassim ( Team Lead ) 
Boucenna Rabah 
Kaizra Yacine 
Khaled Mohammed
Chadli Mohamed Abdelillah

| Instructor: Dr. Meziane Iftene.*
