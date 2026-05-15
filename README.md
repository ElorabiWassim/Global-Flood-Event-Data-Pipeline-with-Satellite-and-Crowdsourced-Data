# Global Flood Event Data Pipeline

An automated, reproducible data pipeline that aggregates historical and
near-real-time flood-event data from satellite observations, government
disaster databases, humanitarian news feeds, and public social-media flood
signals into a single, unified, analysis-ready PostGIS / H3 database, exposed
through a FastAPI service and an interactive dashboard.

## 1. Architecture

```
                           +--------------------------+
                           |  Apache Airflow (DAG)    |
                           |  flood_event_pipeline    |
                           +--------------------------+
                                    |
        +---------+--------------+-------+----------+--------------+----------+
        |         |              |       |          |              |          |
   Dartmouth_FO  Dartmouth_      Copernicus EMS   EM-DAT      ReliefWeb API  Bluesky
   (HDX, frozen) MasterList     (Rapid Mapping)   (CRED HDX)                 search API
                 (live, DFO)
        |         |              |       |          |              |          |
        v         v              v       v          v              v          v
   data/raw/<source>/  (CSV / XLSX / JSON / SHP snapshots + .meta.json sidecars)
        \____________________  \_____  ____________________/                 |
                              \      \/                                      |
                          INSERT into raw.<source>_events                    |
                          raw.social_media_posts  <-------------------------/
                                  |
                  transformations/transform.py
                     |                            |
                     v                            v
        staging.flood_events              staging.social_flood_signals
        (canonical, H3 + PostGIS)         (filtered social observations)
                                  |
                  transformations/marts.py
                                  |
                                  v
              marts.flood_events           (raw projection, audit)
              marts.flood_events_unique    (deduped, canonical for analytics)
              marts.flood_events_by_*      (rollups built on _unique)
              marts.social_flood_signals
              marts.social_signals_by_country_day
              marts.flood_events_with_social_signals
                                  |
                                  v
                          FastAPI + dashboard (api/main.py)
```

### Schemas

| Schema    | Purpose                                                        |
|-----------|----------------------------------------------------------------|
| `raw`     | Untouched ingestion. One JSONB row per source row / social post + audit log. |
| `staging` | Canonical unified `flood_events` table and normalized `social_flood_signals`. |
| `marts`   | API-ready views built on top of `staging`, including social rollups. |

The full DDL lives in [`db/schema.sql`](./db/schema.sql) and is applied
idempotently by the pipeline's first task.

## 2. Repository layout

```
.
├── api/                                       # FastAPI service
│   ├── main.py
│   └── Dockerfile
├── airflow/                                   # Airflow runtime state only
│   ├── logs/                                  #   (logs + plugins; DAGs live
│   └── plugins/                               #    at root ./dags)
├── config/                                    # env-driven settings
│   ├── __init__.py
│   └── settings.py
├── dags/                                      # Airflow DAGs
│   └── flood_event_pipeline_dag.py
├── data/
│   ├── raw/<source>/                          # downloaded files + .meta.json
│   ├── processed/
│   └── logs/data_quality_report.md
├── db/                                        # database layer
│   ├── __init__.py
│   ├── client.py                              # SQLAlchemy engine + helpers
│   └── schema.sql                             # canonical DDL
├── dbt/                                       # optional dbt project
├── docs/data_sources.md
├── ingestion/                                 # raw-load layer
│   ├── __init__.py
│   ├── common.py
│   ├── ingest_dartmouth.py            # DFO HDX shapefile (frozen 2019)
│   ├── ingest_glofas.py               # legacy filename; pulls DFO live
│   │                                  #   MasterList (Dartmouth_MasterList).
│   │                                  #   Real GloFAS reanalysis would
│   │                                  #   feed a separate raw table.
│   ├── ingest_copernicus_ems.py
│   ├── ingest_emdat.py
│   ├── ingest_reliefweb.py
│   └── ingest_bluesky.py             # public Bluesky flood-signal ingestion
├── requirements/base.txt
├── scripts/                                   # one-off utility scripts
│   └── _make_status_pdf.py
├── transformations/                           # raw -> staging -> marts
│   ├── __init__.py
│   ├── transform.py
│   ├── social_geo.py                 # lightweight place/country inference
│   └── marts.py
├── validation/                                # data-quality layer
│   ├── __init__.py
│   └── data_quality.py
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
| `CDS_API_URL`/`CDS_API_KEY` | Optional Copernicus CDS credentials. Real GloFAS reanalysis is **not yet wired** — these are kept as a placeholder for a future ingester. |
| `ENABLE_SOCIAL_MEDIA_INGESTION` | Enables the optional Bluesky task inside the Airflow DAG. |
| `BLUESKY_HANDLE` / `BLUESKY_APP_PASSWORD` | Optional Bluesky App Password credentials. When set, search uses the authenticated `bsky.social` PDS endpoint instead of the public AppView. |
| `BLUESKY_MAX_POSTS` | Maximum normalized posts to upsert per Bluesky ingestion run. |
| `BLUESKY_PER_QUERY_LIMIT` | Per-keyword search page size sent to Bluesky. |
| `BLUESKY_LOOKBACK_HOURS` | Rolling time window for scheduled Bluesky ingestion runs. |

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

### Running only the API + social pipeline with Docker

If I only need to verify the social-media integration and dashboard before a
push, I can run the lightweight API image and execute ingestion/transform as
one-off containers:

```bash
# Build and start the FastAPI service
docker compose build api
docker compose up -d --no-deps api

# Ingest public Bluesky flood posts into raw.social_media_posts
docker run --rm --env-file .env -v "${PWD}:/app" -w /app -e PYTHONPATH=/app \
  global-flood-event-data-pipeline-with-satellite-and-crowdsourced-data-api \
  python -m ingestion.ingest_bluesky

# Transform social posts and refresh marts
docker run --rm --env-file .env -v "${PWD}:/app" -w /app -e PYTHONPATH=/app \
  global-flood-event-data-pipeline-with-satellite-and-crowdsourced-data-api \
  python -c "from transformations.transform import transform_social_media_posts; from transformations.marts import refresh_marts; print(transform_social_media_posts()); refresh_marts()"
```

## 5. Running locally (without Docker)

```bash
# from project root
python -m venv .venv && source .venv/bin/activate         # or .venv\Scripts\activate on Windows
pip install -r requirements/base.txt

# 1) Create schemas + tables
python -c "from db.client import apply_schema_sql; apply_schema_sql()"

# 2) Run a single source (or all)
python -m ingestion.ingest_dartmouth
python -m ingestion.ingest_reliefweb
python -m ingestion.ingest_bluesky
# ...

# 3) Transform raw -> staging
python -m transformations.transform

# 4) Build marts
python -m transformations.marts

# 5) Run DQ checks (writes data/logs/data_quality_report.md)
python -m validation.data_quality

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

-- social-media flood signal counts
SELECT COUNT(*) FROM raw.social_media_posts;
SELECT platform, COUNT(*) FROM staging.social_flood_signals GROUP BY platform;
SELECT * FROM marts.social_signals_by_country_day
ORDER BY signal_date DESC, signal_count DESC
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
curl "http://localhost:8000/flood-events/with-social-signals?limit=5"
curl "http://localhost:8000/social-signals?limit=5"
curl "http://localhost:8000/analytics/frequency-by-basin?basin=Vietnam"
curl "http://localhost:8000/analytics/social-signals/by-platform"
curl "http://localhost:8000/analytics/social-signals/by-country-day?limit=20"
```

Interactive Swagger UI: <http://localhost:8000/docs>.

Interactive dashboard: <http://localhost:8000/>. The dashboard includes a
social-signals KPI card and endpoint explorer buttons for the social routes.

## 8. Data sources

See [`docs/data_sources.md`](./docs/data_sources.md) for the full list of
source URLs, ingestion mechanisms, license / access constraints, and
fallback behaviour.

The social-media branch currently targets Bluesky. It searches multilingual
flood-related keywords, stores retained posts in `raw.social_media_posts`,
normalizes them into `staging.social_flood_signals`, and exposes them through
the social marts and API routes listed above.

## 9. Known limitations

- **GloFAS reanalysis grids** are **not** ingested today. The file named
  `ingestion/ingest_glofas.py` actually pulls the Dartmouth Flood
  Observatory live MasterList — a different vintage of the same archive
  that `ingest_dartmouth.py` reads from HDX. Both are surfaced as the
  `Dartmouth_FO` and `Dartmouth_MasterList` sources respectively, and the
  `marts.flood_events_unique` view dedupes their large Register#-overlap.
  A real Copernicus GloFAS branch can be wired via the CDS API.
- **EM-DAT** requires a free user account; bulk download is gated behind
  a session cookie. The pipeline ships with a CSV seed and accepts a
  signed URL via `EMDAT_DOWNLOAD_URL`.
- **Copernicus EMS** does not publish a stable public JSON feed of
  activations. We use the bundled CSV (`activations.csv`) plus an
  optional `COPERNICUS_EMS_FEED_URL` override.
- **Polygon geometries**: only point geometries are persisted today.
  When polygon shapefiles are available, store the polygon and use its
  centroid for H3 indexing — this is documented inline in `transform.py`.
- **DFO `Displaced` semantics**: DFO's `Displaced` column is broader
  than "permanently displaced" (it includes precautionary evacuees and
  exposed populations). Compare per-event values against EM-DAT for a
  stricter definition.
- **Social-media precision**: Bluesky posts are public situational signals,
  not authoritative disaster records. The pipeline uses flood keywords,
  context terms, political/metaphorical exclusion rules, confidence scores,
  and lightweight place-name inference to reduce noise, but high-impact
  signals should still be cross-checked against authoritative sources.
- **Social-media geolocation**: Most public posts do not include exact
  coordinates. The current implementation infers country/place only when the
  text contains reliable country, adjective, US state, timezone, or city-state
  patterns. H3 indexing is only available when coordinates exist.

## 10. Recommended next steps

1. Add a real CDS-API GloFAS branch into a separate raw table
   (`raw.glofas_reanalysis`) and a separate `_normalize_glofas_reanalysis`
   function. The placeholder is already in `ingest_glofas.py`.
2. Replace the `SequentialExecutor` with `LocalExecutor` + a Postgres
   metadata DB once the workload exceeds one task at a time.
3. Add Great Expectations / Soda checks alongside `data_quality.py`.
4. Re-evaluate the dedupe strategy in `marts.flood_events_unique` if a
   future source publishes events under non-DFO IDs that nonetheless
   cover the same floods (currently we only dedupe across the DFO
   Register# space).
5. Expand social-media ingestion beyond Bluesky with source-specific adapters
   that write into the same `raw.social_media_posts` contract.
6. Replace the lightweight rule-based social geocoder with a gazetteer-backed
   or NER-based approach for better place extraction and event matching.
