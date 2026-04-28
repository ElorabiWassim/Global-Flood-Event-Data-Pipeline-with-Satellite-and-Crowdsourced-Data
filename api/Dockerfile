# Lightweight image for the FastAPI flood-events service.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System libs for psycopg2. We deliberately avoid GDAL/GEOS — the API only
# reads from PostGIS server-side, so no native geo deps are needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/base.txt ./requirements/base.txt
RUN pip install --upgrade pip && pip install -r requirements/base.txt

# Only the packages the API actually imports.
COPY config ./config
COPY db     ./db
COPY api    ./api

ENV PYTHONPATH=/app

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
