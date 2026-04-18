# EM-DAT Flood Data Source Setup

This project now includes the missing plumbing needed to run the EM-DAT flood ingestion path with Supabase Postgres, FastAPI, and Airflow.

## What is included

- EM-DAT ingestion script: `python -m ingestion.emdat`
- Supabase database bootstrap from `shema.sql`
- FastAPI query service at `http://localhost:8000`
- Airflow DAG `emdat_flood_ingestion`
- Docker Compose services for PostGIS, API, ingestion, and Airflow

## Local Python setup

```bash
pip install -r requirements/base.txt
python test_transform.py
python testvalidation.py
```

To load the EM-DAT CSV into the database:

```bash
python -m ingestion.emdat
```

## Docker setup

Start the stack:

```bash
docker compose up --build
```

Run ingestion once:

```bash
docker compose run --rm emdat-ingestion
```

Useful endpoints:

- API health: `http://localhost:8000/health`
- EM-DAT events: `http://localhost:8000/events`
- Airflow UI: `http://localhost:8080`

Default Airflow login:

- Username: `admin`
- Password: `admin`

## Environment notes

Docker now uses the database credentials in `.env` directly. That means the API, the EM-DAT ingestion container, and Airflow all point at the Supabase-hosted Postgres database defined by `DATABASE_URL`.
