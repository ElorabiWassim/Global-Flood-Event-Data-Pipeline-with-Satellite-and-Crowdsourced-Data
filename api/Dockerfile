# Lightweight image for the FastAPI flood-events service.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System libs needed by geopandas / shapely / psycopg2.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libgeos-dev \
        libproj-dev \
        gdal-bin \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/base.txt ./requirements/base.txt
RUN pip install --upgrade pip && pip install -r requirements/base.txt

COPY scripts ./scripts
COPY api     ./api
COPY schema.sql ./schema.sql

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
