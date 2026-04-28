import sys
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from transforms.reliefweb import transform_reliefweb_record
from validation.models.reliefweb import ReliefWebEvent


engine = create_engine(settings.resolved_database_url, pool_pre_ping=True)


STATE_FILE = Path("data/.reliefweb_last_run.txt")

INSERT_SQL = text("""
    INSERT INTO flood_events (
        source, source_event_id, event_name, main_cause,
        date_start, date_end, country, latitude, longitude,
        geometry,
        deaths, displaced, affected,
        severity, flood_impact_index,
        glide_number, url, h3_index, river_basin
    ) VALUES (
        :source, :source_event_id, :event_name, :main_cause,
        :date_start, :date_end, :country, :latitude, :longitude,
        CASE
            WHEN :longitude IS NOT NULL AND :latitude IS NOT NULL
            THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
            ELSE NULL
        END,
        :deaths, :displaced, :affected,
        :severity, :flood_impact_index,
        :glide_number, :url, :h3_index, :river_basin
    )
    ON CONFLICT (source, source_event_id) DO NOTHING
""")


# ── State helpers ──────────────────────────────────────────────────────────

def read_last_run() -> datetime | None:
    if STATE_FILE.exists():
        try:
            return datetime.fromisoformat(STATE_FILE.read_text().strip())
        except ValueError:
            pass
    return None


def save_last_run(ts: datetime):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(ts.isoformat())


# ── API helpers ────────────────────────────────────────────────────────────

def build_filter(since: datetime | None) -> dict:
    flood_filter = {"field": "type.name", "value": "Flood"}
    if since is None:
        return flood_filter
    return {
        "operator": "AND",
        "conditions": [
            flood_filter,
            {
                "field": "date.created",
                "value": {"from": since.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
            }
        ]
    }


def fetch_raw(since: datetime | None) -> list[dict]:
    records, offset, limit = [], 0, 1000

    while True:
        payload = {
            "limit": limit,
            "offset": offset,
            "filter": build_filter(since),
            "fields": {
                "include": ["name", "date", "country", "status", "type", "glide", "url"]
            },
            "sort": ["date.created:desc"]
        }
        resp = requests.post(
            "https://api.reliefweb.int/v2/disasters",
            params={"appname": settings.reliefweb_appname},
            json=payload,
            timeout=30
        )
        if resp.status_code == 403:
            print("ERROR 403: appname not approved.")
            sys.exit(1)
        resp.raise_for_status()

        data  = resp.json()
        items = data.get("data", [])
        total = data.get("totalCount", 0)
        print(f"  Fetched {offset + len(items)} / {total}")

        for item in items:
            f  = item.get("fields", {})
            cs = f.get("country", [])
            di = f.get("date", {})
            raw_date = di.get("event") or di.get("created")

            try:
                parsed_date = pd.to_datetime(raw_date, utc=True).tz_convert(None)
            except Exception:
                parsed_date = None

            records.append({
                "source_event_id": str(item.get("id", "")),
                "event_name":      f.get("name", ""),
                "date_start":      parsed_date,
                "country":         ", ".join(c.get("name", "") for c in cs),
                "glide_number":    f.get("glide") or None,
                "url":             f.get("url") or None,
                "latitude":        None,
                "longitude":       None,
            })

        offset += len(items)
        if offset >= total or not items:
            break
        time.sleep(0.5)

    return records



def main(mode: str = "incremental"):
    run_time = datetime.now(timezone.utc).replace(tzinfo=None)

    since = None
    if mode == "incremental":
        since = read_last_run()
        if since is None:
            print("[reliefweb] No previous run — switching to full mode.")
            mode = "full"

    print(f"\n[reliefweb] mode={mode}  since={since}")
    raw_records = fetch_raw(since if mode == "incremental" else None)
    print(f"[reliefweb] {len(raw_records)} records fetched from API")

    inserted, skipped = 0, 0
    batch_size = 500

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            for record in raw_records:
                # Skip rows with no date or no ID
                if record["date_start"] is None or not record["source_event_id"]:
                    skipped += 1
                    continue

                payload   = transform_reliefweb_record(record, settings.h3_resolution)
                validated = ReliefWebEvent(**payload)

                conn.execute(INSERT_SQL, validated.model_dump())
                inserted += 1

                if inserted <= 3:
                    print(f"  Sample: {validated.source_event_id} | {validated.country} | {validated.date_start}")

                if inserted % batch_size == 0:
                    trans.commit()
                    print(f"  Committed {inserted} rows so far...")
                    trans = conn.begin()

            trans.commit()
        except Exception:
            trans.rollback()
            raise

    save_last_run(run_time)
    print(f"[reliefweb] Done — inserted: {inserted}, skipped: {skipped}")
    return inserted


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "incremental"], default="full")
    args = parser.parse_args()
    main(mode=args.mode)