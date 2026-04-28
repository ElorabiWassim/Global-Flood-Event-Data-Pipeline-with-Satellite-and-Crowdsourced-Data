# Data sources

This pipeline ingests flood-event data from five distinct providers. Some
sources are fully public; others gate downloads behind registration. Where
no public download URL exists, the pipeline ships a **seed CSV** so the
DAG can run end-to-end out of the box, and a clearly-marked extension
point is left for operators to plug in real credentials.

| Source              | Format            | Public access? | Implementation                              |
|---------------------|-------------------|----------------|---------------------------------------------|
| Dartmouth FO        | XLSX / CSV        | Yes            | `ingestion/ingest_dartmouth.py`             |
| GloFAS / GFD        | CSV (CDS for live)| Partial        | `ingestion/ingest_glofas.py`                |
| Copernicus EMS      | HTML / CSV        | Partial        | `ingestion/ingest_copernicus_ems.py`        |
| EM-DAT              | CSV / XLSX        | No (login)     | `ingestion/ingest_emdat.py`                 |
| ReliefWeb           | JSON REST API     | Yes            | `ingestion/ingest_reliefweb.py`             |

---

## 1. Dartmouth Flood Observatory (DFO)

- Site: <https://floodobservatory.colorado.edu/>
- Archive landing page: <https://floodobservatory.colorado.edu/Archives/>
- Direct file (canonical): `https://floodobservatory.colorado.edu/Archives/FloodArchive.xlsx`
- License: free for research use, attribution requested.
- Implementation: tries to download the canonical `FloodArchive.xlsx`,
  falls back to the seed `data/raw/dartmouth/dartmouth_floods.csv` if
  the URL is unreachable.

## 2. GloFAS / Global Active Archive of Large Floods

There are TWO related products under this label:

1. **GloFAS** (Copernicus CEMS) — gridded reanalysis + forecast. Requires
   a free CDS API account: <https://cds.climate.copernicus.eu>.
   - Set `CDS_API_URL` and `CDS_API_KEY` in `.env`.
   - The placeholder branch in `ingestion/ingest_glofas.py`
     documents where to add the `cdsapi` client call.

2. **GFD / Global Active Archive of Large Floods** (Brakenridge / DFO) —
   simple per-event CSV. Public.
   - Public URL (varies): `https://floodobservatory.colorado.edu/temp/MasterListrev.xlsx`
   - The pipeline always loads this CSV as the immediate source so users
     have queryable data even without CDS credentials.

## 3. Copernicus Emergency Management Service (EMS) Rapid Mapping

- List of activations: <https://emergency.copernicus.eu/mapping/list-of-activations-rapid>
- License: open / attribution required.
- There is no fully-stable JSON API: institutions usually scrape the HTML
  list or consume per-activation product packages.
- Implementation:
  - Tries `COPERNICUS_EMS_FEED_URL` if set (operator can supply a CSV
    proxy URL).
  - Falls back to the seed `data/raw/copernicus_ems/activations.csv`.
  - Filters rows where `Event Type` contains "Flood" so we don't pollute
    the staging table with earthquakes / fires.

## 4. EM-DAT (CRED, UCLouvain)

- Site: <https://www.emdat.be/>
- Public portal: <https://public.emdat.be/> (gated, requires registration for
  per-event access).
- License: free for non-commercial research.
- Implementation (priority order):
  1. If `EMDAT_DOWNLOAD_URL` is set, the pipeline streams that URL (operator
     can supply a signed per-event export or institutional mirror).
  2. Otherwise the public CRED-published HDX dataset
     [`emdat-country-profiles`](https://data.humdata.org/dataset/emdat-country-profiles)
     is downloaded as XLSX and treated as the canonical source.
  3. As a last resort, `data/raw/emdat/emdat_floods.csv` (bundled seed CSV).
- **Granularity caveat (HDX path):** the HDX XLSX is `(Year × Country ×
  Disaster Subtype)` *aggregated yearly stats*, not per-event records. The
  ingestor expands each aggregate row into a synthetic event with:
  - `DisNo. = "EMDAT-HDX-{Year}-{ISO}-{slug(Subtype)}"`
  - `Start = Jan 1 / End = Dec 31` of that year (year-only date precision)
  - No `Latitude` / `Longitude` / `River Basin` / `Magnitude`
  - `Total Deaths`, `Total Affected`, `Total Damage (USD)` come straight
    from the EM-DAT totals.
- All paths filter to rows where `Disaster Type` contains "Flood".

## 5. ReliefWeb

- Public REST API: <https://api.reliefweb.int/v2/disasters>
- License: open. Only requires a polite `appname` query parameter.
- Implementation:
  - Calls
    `GET /v2/disasters?appname=<RELIEFWEB_APPNAME>&filter[field]=type&filter[value]=Flood&limit=500`.
  - Persists the JSON response under `data/raw/reliefweb/reliefweb_<batch>.json`.
  - Falls back to the seed CSV `data/raw/reliefweb/reliefweb_floods.csv`
    if the API is unreachable.

---

## Audit metadata

Every download writes a `<file>.meta.json` sidecar containing:

- `source` (e.g. `Dartmouth_FO`)
- `source_url` (the URL that was actually fetched)
- `file_path`
- `checksum` (SHA-256)
- `bytes_downloaded`
- `downloaded_at` (ISO 8601 UTC)
- `used_fallback` / `fallback_reason`

The same information is mirrored in `raw.ingestion_log` (one row per
source per DAG run).
