import pandas as pd
import h3


def safe_str(value):
    if pd.isna(value) or value == "":
        return None
    return str(value).strip()


def safe_float(value):
    if pd.isna(value) or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def safe_int(value):
    if pd.isna(value) or value == "":
        return 0
    try:
        return int(float(value))
    except Exception:
        return 0


def build_date(year, month, day):
    if pd.isna(year):
        return None
    y = int(year)
    m = int(month) if not pd.isna(month) else 1
    d = int(day) if not pd.isna(day) else 1
    return f"{y:04d}-{m:02d}-{d:02d}"


def compute_severity(deaths, displaced, affected, total_damage):
    score = (
        min(deaths / 1000, 1.0) * 0.4
        + min(displaced / 100000, 1.0) * 0.2
        + min(affected / 500000, 1.0) * 0.2
        + min(total_damage / 1000000, 1.0) * 0.2
    )
    return round(score, 4)


def compute_flood_impact_index(deaths, displaced, affected):
    return round(
        deaths * 1.0 +
        displaced * 0.01 +
        affected * 0.001,
        4
    )


def compute_h3_index(latitude, longitude, resolution):
    if latitude is None or longitude is None:
        return None
    return h3.latlng_to_cell(latitude, longitude, resolution)


def transform_emdat_row(row, h3_resolution):
    source_event_id = str(row.get("DisNo.", "")).strip()

    event_name = safe_str(row.get("Event Name"))
    if event_name is None:
        event_name = f"EM-DAT Flood {source_event_id}"

    main_cause = safe_str(row.get("Disaster Subtype"))
    if main_cause is None:
        main_cause = safe_str(row.get("Disaster Type"))

    latitude = safe_float(row.get("Latitude"))
    longitude = safe_float(row.get("Longitude"))

    deaths = safe_int(row.get("Total Deaths"))
    displaced = safe_int(row.get("No. Homeless"))
    affected = safe_int(row.get("Total Affected"))
    total_damage = safe_float(row.get("Total Damage ('000 US$)")) or 0.0

    date_start = build_date(row.get("Start Year"), row.get("Start Month"), row.get("Start Day"))
    date_end = build_date(row.get("End Year"), row.get("End Month"), row.get("End Day"))

    return {
        "source": "emdat",
        "source_event_id": source_event_id,
        "event_name": event_name,
        "main_cause": main_cause,
        "date_start": date_start,
        "date_end": date_end,
        "country": safe_str(row.get("Country")),
        "latitude": latitude,
        "longitude": longitude,
        "deaths": deaths,
        "displaced": displaced,
        "affected": affected,
        "severity": compute_severity(deaths, displaced, affected, total_damage),
        "flood_impact_index": compute_flood_impact_index(deaths, displaced, affected),
        "glide_number": None,
        "url": None,
        "h3_index": compute_h3_index(latitude, longitude, h3_resolution),
        "river_basin": safe_str(row.get("River Basin")),
    }