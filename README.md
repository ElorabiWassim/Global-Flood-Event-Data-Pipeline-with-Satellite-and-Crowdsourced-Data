# Global Flood Event Data Pipeline

An automated, reproducible data pipeline that aggregates historical and
near-real-time flood-event data from satellite observations, government
disaster databases, and crowd-sourced news feeds into a single, unified,
analysis-ready PostGIS / H3 database, exposed through a FastAPI service.

## 1. Architecture

```
                           +--------------------------+
                           |  Apache Airflow (DAG)    |
                           |  flood_event_pipeline    |
                           +--------------------------+
                                    |
        +---------+---------+-------+--------+----------+
        |         |         |                |          |
   Dartmouth   GloFAS   Copernicus EMS    EM-DAT   ReliefWeb API
        |         |         |                |          |
        v         v         v                v          v
   data/raw/<source>/  (CSV / XLSX / JSON snapshots + .meta.json sidecars)
        \____________________  \_____  ____________________/
                              \      \/
                          INSERT into raw.<source>_events  (JSONB payload)
                                  |
                       scripts/transform.py
                                  |
                                  v
                     staging.flood_events  (canonical, H3 + PostGIS)
                                  |
                       scripts/marts.py
                                  |
                                  v
                    marts.* views    --->  FastAPI (api/main.py)
```

### Schemas

| Schema    | Purpose                                                        |
|-----------|----------------------------------------------------------------|
| `raw`     | Untouched ingestion. One JSONB row per source row + audit log. |
| `staging` | Canonical unified `flood_events` table (defined in `schema.sql`). |
| `marts`   | API-ready views built on top of `staging`.                     |

The full DDL lives in [`schema.sql`](./schema.sql) and is applied
idempotently by the pipeline's first task.

## 2. Repository layout

```
.
├── airflow/dags/flood_event_pipeline_dag.py   # Airflow DAG
├── api/                                       # FastAPI service
│   ├── main.py
│   └── Dockerfile
├── data/
│   ├── raw/<source>/                          # downloaded files + .meta.json
│   ├── processed/
│   └── logs/data_quality_report.md
├── dbt/                                       # optional dbt project
├── docs/data_sources.md
├── requirements/base.txt
├── schema.sql                                 # source of truth
├── scripts/
│   ├── config.py                              # env-driven settings
│   ├── db.py                                  # SQLAlchemy engine + helpers
│   ├── transform.py                           # raw  -> staging
│   ├── marts.py                               # staging -> marts
│   ├── data_quality.py                        # DQ checks + Markdown report
│   └── ingestion/
│       ├── common.py
│       ├── ingest_dartmouth.py
│       ├── ingest_glofas.py
│       ├── ingest_copernicus_ems.py
│       ├── ingest_emdat.py
│       └── ingest_reliefweb.py
├── docker-compose.yml
└── .env                                       # NOT committed in production
```

## 3. Configuration

All credentials and tunables come from `.env`:

| Variable             | Purpose                                              |
|----------------------|------------------------------------------------------|
| `DATABASE_URL`       | Full SQLAlchemy URL (preferred).                     |
| `POSTGRES_*`         | Fallback parts (host, user, password, port, db).     |
| `H3_RESOLUTION`      | H3 cell resolution used by transform.py (default 7). |
| `API_HOST`/`API_PORT`| FastAPI bind config.                                 |
| `RELIEFWEB_APPNAME`  | App identifier sent to the public ReliefWeb API.     |
| `EMDAT_DOWNLOAD_URL` | Optional signed URL for EM-DAT bulk export.          |
| `CDS_API_URL`/`CDS_API_KEY` | Optional Copernicus CDS credentials (GloFAS).|

## 4. Running with Docker

```bash
# 1) Build and start everything
docker compose up --build

# 2) Open Airflow UI
#    http://localhost:8081  (admin / admin)

# 3) Open the API
#    http://localhost:8000/docs
```

The first start of the airflow container will:
- run `airflow db migrate`
- create the `admin` user (idempotent)
- install pipeline dependencies declared in `_PIP_ADDITIONAL_REQUIREMENTS`
- launch `airflow standalone` (web + scheduler in one process)

### Triggering the DAG

In the Airflow UI, locate **`flood_event_pipeline`**, toggle it on, then
click the ▶ "Trigger DAG" button. The DAG is also scheduled `@daily`.

CLI alternative:

```bash
docker exec -it flood_airflow airflow dags trigger flood_event_pipeline
```

## 5. Running locally (without Docker)

```bash
# from project root
python -m venv .venv && source .venv/bin/activate         # or .venv\Scripts\activate on Windows
pip install -r requirements/base.txt

# 1) Create schemas + tables
python -c "from scripts.db import apply_schema_sql; apply_schema_sql()"

# 2) Run a single source (or all)
python -m scripts.ingestion.ingest_dartmouth
python -m scripts.ingestion.ingest_reliefweb
# ...

# 3) Transform raw -> staging
python -m scripts.transform

# 4) Build marts
python -m scripts.marts

# 5) Run DQ checks (writes data/logs/data_quality_report.md)
python -m scripts.data_quality

# 6) Serve the API
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

## 6. Querying the database

Connect with any Postgres client to the URL in `.env`. Quick smoke checks:

```sql
-- counts per source
SELECT source, COUNT(*) FROM staging.flood_events GROUP BY source;

-- top 10 deadliest events
SELECT source, country, date_start, deaths
FROM marts.flood_events
ORDER BY deaths DESC NULLS LAST
LIMIT 10;

-- frequency by basin (country proxy) since 2010
SELECT * FROM marts.flood_frequency_by_basin
WHERE year >= 2010
ORDER BY event_count DESC
LIMIT 20;
```

## 7. Querying the API

Once the FastAPI container is up:

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/flood-events?limit=5"
curl "http://localhost:8000/flood-events/by-region?country=Vietnam"
curl "http://localhost:8000/flood-events/by-time?start=2020-01-01&end=2020-12-31"
curl "http://localhost:8000/flood-events/by-severity?min_severity=2"
curl "http://localhost:8000/flood-events/by-h3?h3_index=87283472bffffff"
curl "http://localhost:8000/analytics/frequency-by-basin?basin=Vietnam"
```

Interactive Swagger UI: <http://localhost:8000/docs>.

## 8. Data sources

See [`docs/data_sources.md`](./docs/data_sources.md) for the full list of
source URLs, ingestion mechanisms, license / access constraints, and
fallback behaviour.

## 9. Known limitations

- **GloFAS reanalysis grids** require the Copernicus CDS API key. Without
  one, the pipeline falls back to the Global Active Archive of Large Floods
  CSV (no per-event lat/lon, hence no H3).
- **EM-DAT** requires a free user account; bulk download is gated behind
  a session cookie. The pipeline ships with a CSV seed and accepts a
  signed URL via `EMDAT_DOWNLOAD_URL`.
- **Copernicus EMS** does not publish a stable public JSON feed of
  activations. We use the bundled CSV (`activations.csv`) plus an
  optional `COPERNICUS_EMS_FEED_URL` override.
- **Basin-level analysis** is currently approximated by country because
  the unified schema in `schema.sql` does not include a dedicated basin
  column. EM-DAT's `River Basin` field is preserved in the raw payload
  and can be promoted later (a one-line schema change + dbt model).
- **Polygon geometries**: only point geometries are persisted today.
  When polygon shapefiles are available, store the polygon and use its
  centroid for H3 indexing — this is documented inline in `transform.py`.

## 10. Recommended next steps

1. Promote EM-DAT `River Basin` to a real column on `staging.flood_events`
   so `marts.flood_frequency_by_basin` becomes hydrologically accurate.
2. Add a real CDS-API GloFAS branch (`scripts/ingestion/ingest_glofas.py`
   has the placeholder).
3. Wire dbt into the DAG (`dbt run --profiles-dir dbt`) once `dbt-core`
   is added to the airflow image.
4. Replace the `SequentialExecutor` with `LocalExecutor` + a Postgres
   metadata DB once the workload exceeds one task at a time.
5. Add Great Expectations / Soda checks alongside `data_quality.py`.
