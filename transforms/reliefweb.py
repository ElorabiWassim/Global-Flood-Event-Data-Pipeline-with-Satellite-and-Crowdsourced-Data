import h3
import pandas as pd


def safe_str(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return None
    return str(value).strip()


def safe_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def compute_h3_index(latitude, longitude, resolution: int) -> str | None:
    if latitude is None or longitude is None:
        return None
    try:
        return h3.latlng_to_cell(latitude, longitude, resolution)
    except Exception:
        return None


def transform_reliefweb_record(record: dict, h3_resolution: int) -> dict:
    latitude  = safe_float(record.get("latitude"))
    longitude = safe_float(record.get("longitude"))

    return {
        "source":             "reliefweb",
        "source_event_id":    str(record.get("source_event_id", "")).strip(),
        "event_name":         safe_str(record.get("event_name")),
        "main_cause":         None,
        "date_start":         record.get("date_start"),
        "date_end":           None,
        "country":            safe_str(record.get("country")),
        "latitude":           latitude,
        "longitude":          longitude,
        "deaths":             0,
        "displaced":          0,
        "affected":           0,
        "severity":           None,
        "flood_impact_index": None,
        "glide_number":       safe_str(record.get("glide_number")),
        "url":                safe_str(record.get("url")),
        "h3_index":           compute_h3_index(latitude, longitude, h3_resolution),
        "river_basin":        None,
    }