# Project status summary
*Global Flood Event Data Pipeline — generated 2026-04-27, updated 2026-05-06*

## Updates since 2026-04-27

### Test suite
- **64 pytest cases** now pass (`tests/test_db_client.py`, `tests/test_ingestion_common.py`, `tests/test_transform.py`). Covers every `_normalize_*` function, scalar coercers, basin extraction, JSON cleaning, ingestion helpers, and source-name contracts.

### Schema: `river_basin` promoted to a real column
- `staging.flood_events.river_basin` is populated (mainly by EM-DAT, ~9.5k rows). `marts.flood_frequency_by_basin` now uses the real column with country fallback and a `basin_source` flag.

### Interactive dashboard
- New SPA at `api/static/index.html` (Tailwind + Chart.js + Lucide). Served at `/`; the original JSON listing moved to `/api`. Charts: monthly time series, source breakdown, top countries, severity distribution. Filterable events table + endpoint explorer.

### CORS + new analytics endpoint
- `CORSMiddleware` enabled in `api/main.py`. New `GET /analytics/by-source` returns a single-call per-source rollup (used by the dashboard for correct totals).

### **Source rename + dedupe**  *(this is the big one)*
- Investigation in `validation/investigate_displaced.py` found that the source previously labelled **`GloFAS`** is **not** Copernicus GloFAS — it pulls `floodobservatory.colorado.edu/temp/MasterListrev.xlsx`, the **live vintage of the Dartmouth Flood Observatory archive** that `Dartmouth_FO` reads (frozen) from HDX. About **73% of those rows duplicated** `Dartmouth_FO` Register#s, plus an intra-Dartmouth_FO `2507`-vs-`DFO_2507` collision from a previous fallback run.
- Renamed source `GloFAS` → **`Dartmouth_MasterList`** in `ingestion/ingest_glofas.py`, `transformations/transform.py`, the test suite, and the dashboard color map. Raw table name `raw.glofas_events` kept for backwards compatibility.
- Added **`marts.flood_events_unique`** view that picks one canonical row per DFO Register# (prefers `Dartmouth_MasterList` over `Dartmouth_FO`; prefers bare Register# over `DFO_`-prefixed). Non-DFO sources are passed through untouched.
- Rebuilt every rollup mart (`flood_events_by_region`, `flood_events_by_h3`, `flood_events_by_month`, `flood_frequency_by_basin`) on top of `flood_events_unique`. The raw `marts.flood_events` projection is preserved for auditing.
- API endpoints serving event-level data now read from `marts.flood_events_unique`.
- One-time migration (`scripts/migrate_glofas_rename.py`, idempotent) renamed the existing rows in `staging.flood_events` and refreshed the marts.

### Dashboard KPI accuracy
- Two bugs in the dashboard's KPI math fixed: `Total events` was capped at 1000/source (off by ~16k) and `Total deaths` only summed the top-10 countries. Replaced both with the new `/analytics/by-source` rollup.

### Headline numbers, before vs after dedupe

| KPI | Raw (`marts.flood_events`) | Deduped (`marts.flood_events_unique`) | Removed |
|---|---:|---:|---:|
| Events | 21,018 | **16,405** | 4,613 |
| Deaths | 8,657,483 | **7,866,637** | 790,846 |
| Displaced | 1,616,913,200 | **762,424,144** | 854,489,056 |
| Distinct sources | 5 | 5 | — |

The dashboard KPIs now match `marts.flood_events_unique` exactly.

---

## Working (validated end-to-end against Supabase)

### 1. Database schema
- `db/schema.sql` — Idempotent DDL: PostGIS extension, three schemas (`raw` / `staging` / `marts`), 5 raw JSONB tables, `raw.ingestion_log` audit table, canonical `staging.flood_events` with H3 + PostGIS, plus forward-migration `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements that bring any pre-existing tables up to date.

### 2. Configuration & shared DB layer
- `config/settings.py` — Loads `.env`, builds `DATABASE_URL`, exposes `H3_RESOLUTION` and other tunables.
- `db/client.py` — Singleton SQLAlchemy engine tuned for the Supabase pooler (TCP keepalives, `pool_pre_ping`, `pool_recycle=300`), `apply_schema_sql()`, `insert_raw_records()`, `truncate_raw_table()`, `log_ingestion()`, `execute_with_retry()` with exponential backoff on `OperationalError`, and `_clean_for_json()` for NaN-safe payloads.

### 3. Ingestion (5 sources, ~21k rows raw / ~16k deduped)
- `ingestion/common.py` — `download_to()` with SHA-256 + sidecar `.meta.json`, plus `parse_with_fallback()` for parser-time recovery.
- `ingestion/ingest_dartmouth.py` — **4,616 rows** (`Dartmouth_FO`, HDX-frozen 2019 vintage of DFO).
- `ingestion/ingest_glofas.py` — **5,503 rows** (`Dartmouth_MasterList`, live DFO MasterList; **not** Copernicus GloFAS — see Updates section).
- `ingestion/ingest_copernicus_ems.py` — **345 rows** (Rapid Mapping API, filtered to floods).
- `ingestion/ingest_emdat.py` — **8,775 rows** (CRED HDX XLSX, synthetic per-event expansion).
- `ingestion/ingest_reliefweb.py` — **1,779 rows** (REST API v2 with approved appname).

### 4. Unified canonical model
- `transformations/transform.py` — Per-source `_normalize_*` functions, H3 v3 indexing, PostGIS `ST_MakePoint`, upsert on `(source, source_event_id)`. All ~21,018 rows land in `staging.flood_events`; the deduped analytical view collapses DFO Register# overlap down to ~16,405.

### 5. Marts (6 views)
- `transformations/marts.py` — `marts.flood_events` (raw projection, audit), `marts.flood_events_unique` (canonical, deduped), `flood_events_by_region`, `flood_events_by_h3`, `flood_frequency_by_basin`, `flood_events_by_month` (rollups built on `_unique`).

### 6. Data quality
- `validation/data_quality.py` — 7 row-level checks plus a per-source rollup.
- `data/logs/data_quality_report.md` — All 7 checks pass `(OK)` against the loaded data.

### 7. Orchestration
- `dags/flood_event_pipeline_dag.py` — Apply schema → 5 ingestions in parallel → transform → marts → DQ.

### 8. REST API
- `api/main.py` — FastAPI: `/`, `/api`, `/health`, `/flood-events`, `/flood-events/by-region`, `/by-time`, `/by-severity`, `/by-h3`, `/analytics/frequency-by-basin`, `/analytics/by-month`, `/analytics/by-source`. Event-level endpoints read from `marts.flood_events_unique`. CORS enabled.
- `api/static/index.html` — Interactive SPA dashboard (Tailwind + Chart.js + Lucide).
- `api/Dockerfile` — Standalone container image.

### 9. Container orchestration
- `docker-compose.yml` — Airflow with `_PIP_ADDITIONAL_REQUIREMENTS`, individual mounts for each source package (`config/`, `db/`, `ingestion/`, `transformations/`, `validation/`), plus a separate `api` service that only mounts `config/` and `db/`.

### 10. dbt scaffolding (optional)
- `dbt/dbt_project.yml`, `dbt/profiles.yml`
- `dbt/models/sources.yml` — declares the 6 sources.
- `dbt/models/staging/stg_dfo.sql` — example staging model.
- `dbt/models/marts/flood_events.sql` — passthrough mart.

### 11. Documentation
- `README.md` — Architecture diagram, layout, config, run instructions, API examples, known limitations, next steps.
- `docs/data_sources.md` — Per-source URLs, formats, license / access notes, fallback behaviour.

---

## Problems faced (and how they were fixed)

| # | Problem | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | `schema.sql` wasn't valid SQL — it was just a column listing without `CREATE TABLE` | Original file was a sketch | Rewrote `db/schema.sql` as full idempotent DDL |
| 2 | `staging.flood_events` already existed in Supabase from a prior run, missing the new columns (`affected`, `flood_impact_index`, `loaded_at`) | `CREATE TABLE IF NOT EXISTS` silently no-ops when the table is already there | Added `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations + a DO-block for the unique constraint |
| 3 | Dartmouth XLSX URL returned **HTTP 200 with HTML**; `download_to` declared "success" but the parser blew up with `BadZipFile` | A successful HTTP status doesn't guarantee parsable bytes | Added `parse_with_fallback()` in `ingestion/common.py` — retries with the seed file when the parser raises |
| 4 | Supabase pooler killed `INSERT` mid-batch (`server closed the connection unexpectedly`) on big GloFAS / EM-DAT loads | pgBouncer idle-kill plus large multi-row VALUES blocks | Engine-level tuning in `db/client.py`: chunk = 50, TCP keepalives, `pool_pre_ping`, `pool_recycle=300`, plus `execute_with_retry()` with exponential backoff and `_reset_engine()` on failure |
| 5 | `psycopg2.errors.InvalidTextRepresentation: Token "NaN" is invalid` on EM-DAT JSON insert | Pandas serialises empty cells as float `NaN`, which Postgres JSONB rejects (RFC 8259 strict) | Added recursive `_clean_for_json()` plus `json.dumps(..., allow_nan=False)` in `db/client.py` |
| 6 | DQ check showed 1,783 rows with severity > 10 | EM-DAT's "Magnitude" column for floods is **inundated area in km²**, not a normalized severity | Moved EM-DAT `Magnitude` → `flood_impact_index` and set `severity = NULL` for that source in `transformations/transform.py` |
| 7 | `TypeError: Invalid argument(s) 'executemany_values_page_size'` to `create_engine()` | Wrong argument name for SQLAlchemy 2.0 | Switched to `insertmanyvalues_page_size=100` |
| 8 | `geopandas` / `shapely` in requirements would force GDAL / GEOS into the airflow image | Wheels exist but pull heavy native deps | Removed both from `requirements/base.txt` — never imported anyway, since PostGIS handles geometry server-side |
| 9 | Transient DNS failure mid-EM-DAT during testing (`could not translate host name`) | Local network blip | Already covered by `execute_with_retry`; just re-ran the one source |

---

## What's still missing / known limitations

### Data sources
- **Copernicus GloFAS reanalysis grids are not ingested.** What `ingest_glofas.py` actually pulls is the live DFO MasterList; see the Updates section. A real GloFAS reanalysis branch (NetCDF streamflow grids via `cdsapi`) would feed a separate `raw.glofas_reanalysis` table and a separate normalizer.
- **EM-DAT** — free login required for bulk download; pipeline now uses the CRED HDX global XLSX with synthetic per-event expansion. `EMDAT_DOWNLOAD_URL` overrides.
- **Copernicus EMS** — official paged JSON activations API (no scraping).

### Schema gaps
- **River basin** — *resolved.* `staging.flood_events.river_basin` is now a real column populated mainly by EM-DAT, with country fallback in `marts.flood_frequency_by_basin`.
- **Polygon geometries** — only point geometries are stored. Polygon shapefiles from Copernicus EMS or DFO would need a `geometry_polygon GEOMETRY(MultiPolygon, 4326)` column and updated centroid-for-H3 logic.

### Coverage gaps
- **Per-source coordinate availability is uneven**: `Dartmouth_FO` has 100% lat/lon (HDX SHP centroids), EM-DAT has ~16%, `Dartmouth_MasterList` / Copernicus EMS / ReliefWeb have 0%. This is a data-source reality, not a bug.
- **Severity** is only populated for the two DFO sources (1–2 scale). EM-DAT and ReliefWeb don't expose one.
- **DFO `Displaced` semantics** — broader than "permanently displaced" (includes precautionary evacuees and exposed populations). Compare per-event values against EM-DAT for a stricter definition.

### Pipeline / infrastructure
- **dbt isn't wired into the Airflow DAG** — `dbt-core` is now installed in the Airflow image, but no `BashOperator` task runs `dbt run` yet. Models exist standalone in `dbt/`.
- **`SequentialExecutor`** is fine for now but only runs one task at a time. Switching to `LocalExecutor` requires a real metadata DB (Postgres container) instead of the bundled SQLite.
- **Test suite is in place** — *resolved.* 64 pytest cases cover transformers, cleaners, parsers, and source contracts. An integration test against a Postgres test container would still be a useful addition.
- **No Great Expectations / Soda** alongside `data_quality.py` — current checks are inline SQL only, not a versioned suite.

### Repository hygiene
- The codebase has been restructured into logically separated top-level packages: `config/`, `dags/`, `db/`, `ingestion/`, `transformations/`, `validation/`. The old `scripts/` folder now only contains the one-off `_make_status_pdf.py` utility. Empty placeholders that previously lived in those folders have been removed.

### Docker / runtime
- **First-run cost** — `_PIP_ADDITIONAL_REQUIREMENTS` reinstalls dependencies every time the airflow container starts. For production, build a custom Airflow image instead.
- **`.env` is bind-mounted** into the airflow container at startup; ensure it isn't committed.

### Security
- **Default Airflow credentials** are `admin / admin` (intentional for local). Change before any non-local deployment.
- **CORS is permissive** in `api/main.py` — `allow_origins=["*"]` (intentional for the bundled dashboard). Lock down before exposing publicly.

---

## Recommended priority for next session

1. **Add a real Copernicus GloFAS reanalysis branch** into a separate `raw.glofas_reanalysis` table + `_normalize_glofas_reanalysis` function (the placeholder is already in `ingest_glofas.py`).
2. **Wire dbt into the DAG** as a final task — dbt is now installed in the airflow image; just needs a `BashOperator`.
3. **Build a custom Airflow image** so dependencies don't reinstall on every container start.
4. **Add Great Expectations / Soda** alongside `data_quality.py` for a versioned, declarative DQ suite.
