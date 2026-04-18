from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from config.settings import settings
from transforms.emdat import transform_emdat_row
from validation.models.emdat import EmdatEvent

engine = create_engine(settings.resolved_database_url, pool_pre_ping=True)


def main():
    csv_path = Path("data/EM-DAT.csv")
    print("Looking for file at:", csv_path.resolve())

    if not csv_path.exists():
        raise FileNotFoundError(f"Missing file: {csv_path}")

    df = pd.read_csv(csv_path)

    print("Loaded rows:", len(df))
    print("Columns:", list(df.columns))
    print("Unique Disaster Type values:", df["Disaster Type"].dropna().astype(str).unique()[:20])

    df = df[df["Disaster Type"].astype(str).str.contains("flood", case=False, na=False)].copy()

    print("Flood rows after filter:", len(df))

    inserted = 0
    batch_size = 500

    insert_sql = text("""
        insert into flood_events (
            source,
            source_event_id,
            event_name,
            main_cause,
            date_start,
            date_end,
            country,
            latitude,
            longitude,
            geometry,
            deaths,
            displaced,
            affected,
            severity,
            flood_impact_index,
            glide_number,
            url,
            h3_index,
            river_basin
        )
        values (
            :source,
            :source_event_id,
            :event_name,
            :main_cause,
            :date_start,
            :date_end,
            :country,
            :latitude,
            :longitude,
            case
                when :longitude is not null and :latitude is not null
                then ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                else null
            end,
            :deaths,
            :displaced,
            :affected,
            :severity,
            :flood_impact_index,
            :glide_number,
            :url,
            :h3_index,
            :river_basin
        )
        on conflict (source, source_event_id) do nothing
    """)

    with engine.connect() as conn:
        trans = conn.begin()

        try:
            for _, row in df.iterrows():
                payload = transform_emdat_row(row, settings.h3_resolution)

                if payload["date_start"] is None or not payload["source_event_id"]:
                    continue

                validated = EmdatEvent(**payload)

                conn.execute(insert_sql, validated.model_dump())
                inserted += 1

                if inserted <= 5:
                    print(
                        "Inserted sample row:",
                        validated.source_event_id,
                        validated.country,
                        validated.date_start,
                    )

                if inserted % batch_size == 0:
                    trans.commit()
                    print(f"Committed {inserted} rows so far...")
                    trans = conn.begin()

            trans.commit()
            print(f"Inserted {inserted} EM-DAT flood events")

        except Exception:
            trans.rollback()
            raise


if __name__ == "__main__":
    main()
