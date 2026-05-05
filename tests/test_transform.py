"""
Unit tests for transformations.transform.

All targets are pure functions: scalar coercers, basin lookup, and the five
per-source ``_normalize_*`` functions. None of them touch the database, so
these tests run in milliseconds with no fixtures beyond literal payloads.
"""

from __future__ import annotations

from datetime import datetime

from transformations.transform import (
    _find_basin,
    _h3_for,
    _normalize_copernicus_ems,
    _normalize_dartmouth,
    _normalize_emdat,
    _normalize_glofas,
    _normalize_reliefweb,
    _parse_date,
    _to_float,
    _to_int,
    _ymd_to_date,
)


# ---------------------------------------------------------------------------
# Scalar coercers
# ---------------------------------------------------------------------------
class TestToInt:
    def test_plain_int(self):
        assert _to_int(7) == 7

    def test_plain_float_truncates(self):
        assert _to_int(7.9) == 7

    def test_string_with_thousands_separator(self):
        assert _to_int("12,345") == 12345

    def test_empty_string_is_none(self):
        assert _to_int("") is None

    def test_none_is_none(self):
        assert _to_int(None) is None

    def test_garbage_is_none(self):
        assert _to_int("abc") is None
        assert _to_int("--") is None


class TestToFloat:
    def test_plain_float(self):
        assert _to_float(1.5) == 1.5

    def test_string_float(self):
        assert _to_float("2.75") == 2.75

    def test_nan_is_none(self):
        assert _to_float(float("nan")) is None

    def test_inf_is_none(self):
        assert _to_float(float("inf")) is None
        assert _to_float(float("-inf")) is None

    def test_none_is_none(self):
        assert _to_float(None) is None

    def test_empty_string_is_none(self):
        assert _to_float("") is None

    def test_garbage_is_none(self):
        assert _to_float("nope") is None


class TestParseDate:
    def test_iso_string(self):
        ts = _parse_date("2024-03-15")
        assert isinstance(ts, datetime)
        assert ts.year == 2024 and ts.month == 3 and ts.day == 15

    def test_datetime_passthrough(self):
        d = datetime(2020, 1, 1)
        assert _parse_date(d) == d

    def test_none(self):
        assert _parse_date(None) is None

    def test_garbage(self):
        assert _parse_date("not a date") is None


class TestYmdToDate:
    def test_valid_ymd(self):
        d = _ymd_to_date(2020, 5, 15)
        assert d == datetime(2020, 5, 15)

    def test_no_year_returns_none(self):
        assert _ymd_to_date(None, 5, 15) is None

    def test_no_month_defaults_to_january(self):
        d = _ymd_to_date(2020, None, None)
        assert d == datetime(2020, 1, 1)

    def test_clipped_to_valid_range(self):
        # EM-DAT occasionally has month=13 / day=35 — the helper clamps these
        # rather than raising, since the upstream record is still useful.
        d = _ymd_to_date(2020, 13, 35)
        assert d.year == 2020
        assert 1 <= d.month <= 12
        assert 1 <= d.day <= 28


class TestH3For:
    def test_valid_coords(self):
        idx = _h3_for(48.85, 2.35)  # Paris
        assert isinstance(idx, str) and len(idx) > 0

    def test_none_coords(self):
        assert _h3_for(None, 2.35) is None
        assert _h3_for(48.85, None) is None

    def test_out_of_range(self):
        assert _h3_for(100.0, 2.35) is None
        assert _h3_for(48.85, 200.0) is None


# ---------------------------------------------------------------------------
# _find_basin (the helper introduced for river_basin promotion)
# ---------------------------------------------------------------------------
class TestFindBasin:
    def test_em_dat_canonical_field(self):
        assert _find_basin({"River Basin": "Mekong"}) == "Mekong"

    def test_lowercase_key(self):
        assert _find_basin({"basin": "Danube"}) == "Danube"

    def test_skips_inundated_area_keys(self):
        # GFD / DFO sometimes have a "Basin Area" column with a number — we
        # must not return that as the basin name.
        assert _find_basin({"Basin Area km2": 1234, "River Basin": "Niger"}) == "Niger"

    def test_no_basin_returns_none(self):
        assert _find_basin({"country": "France", "deaths": 3}) is None

    def test_empty_value_returns_none(self):
        assert _find_basin({"River Basin": ""}) is None

    def test_non_string_keys_ignored(self):
        # Defensive: should not crash on numeric / None keys (rare in JSONB).
        assert _find_basin({1: "Mekong", "River Basin": "Niger"}) == "Niger"

    def test_numeric_basin_value_rejected(self):
        # EM-DAT occasionally has area numbers in the River Basin column
        # (e.g. ``1190``, ``686734.8``). These must NOT pollute the column.
        assert _find_basin({"River Basin": "1190"}) is None
        assert _find_basin({"River Basin": "686734.8"}) is None

    def test_mostly_numeric_with_alpha_kept(self):
        # Real basins occasionally include numerals (e.g. zone codes).
        assert _find_basin({"River Basin": "Yangtze 2"}) == "Yangtze 2"


# ---------------------------------------------------------------------------
# Normalizers — one happy-path payload each
# ---------------------------------------------------------------------------
class TestNormalizeDartmouth:
    def test_minimal_payload(self):
        rows = _normalize_dartmouth(
            [
                {
                    "ID": "DFO-100",
                    "Began": "2020-01-15",
                    "Ended": "2020-01-20",
                    "Country": "France",
                    "latitude": 48.85,
                    "longitude": 2.35,
                    "Dead": 12,
                    "Severity": 1.5,
                    "MainCause": "Heavy rain",
                }
            ]
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "Dartmouth_FO"
        assert r["source_event_id"] == "DFO-100"
        assert r["country"] == "France"
        assert r["latitude"] == 48.85
        assert r["longitude"] == 2.35
        assert r["deaths"] == 12
        assert r["severity"] == 1.5
        assert r["main_cause"] == "Heavy rain"
        assert r["h3_index"] is not None  # coords present -> H3 generated
        assert r["river_basin"] is None  # Dartmouth has no basin field


class TestNormalizeGlofas:
    def test_minimal_payload(self):
        rows = _normalize_glofas(
            [
                {
                    "ID": "GFD-1",
                    "Start Date": "2019-06-01",
                    "Country": "Bangladesh",
                    "Fatalities": 50,
                    "Severity": 2,
                    "Main Cause": "Monsoon",
                }
            ]
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "Dartmouth_MasterList"
        assert r["source_event_id"] == "GFD-1"
        assert r["country"] == "Bangladesh"
        assert r["deaths"] == 50
        assert r["severity"] == 2.0
        # The DFO MasterList CSV has no per-event coords -> no H3
        assert r["latitude"] is None
        assert r["h3_index"] is None


class TestNormalizeCopernicusEMS:
    def test_emsr_code_is_promoted_to_url(self):
        rows = _normalize_copernicus_ems(
            [
                {
                    "Code": "EMSR512",
                    "Date": "11/03/2024, 16:51",
                    "Country": "Germany",
                    "Title": "Flooding in Bavaria",
                    "Event Type": "Flood",
                }
            ]
        )
        r = rows[0]
        assert r["source"] == "Copernicus_EMS"
        assert r["source_event_id"] == "EMSR512"
        assert "EMSR512" in r["url"]
        assert r["country"] == "Germany"
        assert isinstance(r["date_start"], datetime)

    def test_non_emsr_code_yields_no_url(self):
        rows = _normalize_copernicus_ems(
            [{"Code": "NOTACODE", "Date": "01/02/2024", "Title": "x"}]
        )
        assert rows[0]["url"] is None


class TestNormalizeEMDAT:
    def test_river_basin_extracted(self):
        rows = _normalize_emdat(
            [
                {
                    "DisNo.": "2020-0001-FRA",
                    "Country": "France",
                    "Start Year": 2020,
                    "Start Month": 5,
                    "Start Day": 10,
                    "End Year": 2020,
                    "End Month": 5,
                    "End Day": 15,
                    "Total Deaths": "23",
                    "No. Affected": "1,500",
                    "River Basin": "Seine",
                    "Magnitude": 250.0,
                    "Disaster Subtype": "Riverine flood",
                }
            ]
        )
        r = rows[0]
        assert r["source"] == "EM-DAT"
        assert r["source_event_id"] == "2020-0001-FRA"
        assert r["country"] == "France"
        assert r["river_basin"] == "Seine"
        assert r["deaths"] == 23
        assert r["affected"] == 1500  # comma stripped
        # EM-DAT Magnitude is inundated area, not severity. Critical contract.
        assert r["severity"] is None
        assert r["flood_impact_index"] == 250.0
        assert r["main_cause"] == "Riverine flood"

    def test_missing_basin_falls_back_to_none(self):
        rows = _normalize_emdat(
            [
                {
                    "DisNo.": "2021-0001-IND",
                    "Country": "India",
                    "Start Year": 2021,
                    "Start Month": 7,
                    "Start Day": 1,
                }
            ]
        )
        assert rows[0]["river_basin"] is None

    def test_dirty_numeric_basin_is_dropped(self):
        # Regression: EM-DAT sometimes stores km^2 area values in
        # ``River Basin``. Those must not propagate to the canonical column.
        rows = _normalize_emdat(
            [
                {
                    "DisNo.": "1996-0082-IDN",
                    "Country": "Indonesia",
                    "Start Year": 1996,
                    "Start Month": 1,
                    "Start Day": 1,
                    "River Basin": "1190",
                }
            ]
        )
        assert rows[0]["river_basin"] is None


class TestNormalizeReliefweb:
    def test_minimal_payload(self):
        rows = _normalize_reliefweb(
            [
                {
                    "id": "RW-9999",
                    "title": "Floods in Pakistan",
                    "country": "Pakistan",
                    "date": "2022-08-25",
                    "url": "https://reliefweb.int/disaster/9999",
                }
            ]
        )
        r = rows[0]
        assert r["source"] == "ReliefWeb"
        assert r["source_event_id"] == "RW-9999"
        assert r["country"] == "Pakistan"
        assert r["main_cause"] == "Flood"
        assert isinstance(r["date_start"], datetime)
        assert r["url"] == "https://reliefweb.int/disaster/9999"


# ---------------------------------------------------------------------------
# Cross-cutting contract: every normalizer returns the same set of keys.
# This guards the UPSERT_SQL bind-parameter contract — if a normalizer drops
# or renames a key the upsert will fail with a KeyError at runtime.
# ---------------------------------------------------------------------------
EXPECTED_KEYS = {
    "source", "source_event_id", "event_name", "main_cause",
    "date_start", "date_end",
    "country", "river_basin", "latitude", "longitude",
    "deaths", "displaced", "affected",
    "severity", "flood_impact_index",
    "glide_number", "url", "h3_index",
}


def test_all_normalizers_share_key_contract():
    samples = {
        _normalize_dartmouth: [{"ID": "x", "Began": "2020-01-01"}],
        _normalize_glofas: [{"ID": "x", "Start Date": "2020-01-01"}],
        _normalize_copernicus_ems: [{"Code": "EMSR1", "Date": "01/01/2020"}],
        _normalize_emdat: [
            {"DisNo.": "x", "Start Year": 2020, "Start Month": 1, "Start Day": 1}
        ],
        _normalize_reliefweb: [{"id": "x", "date": "2020-01-01"}],
    }
    for fn, payload in samples.items():
        rows = fn(payload)
        assert len(rows) == 1, f"{fn.__name__} dropped row"
        assert set(rows[0].keys()) == EXPECTED_KEYS, (
            f"{fn.__name__} key drift: "
            f"missing={EXPECTED_KEYS - set(rows[0].keys())} "
            f"extra={set(rows[0].keys()) - EXPECTED_KEYS}"
        )
