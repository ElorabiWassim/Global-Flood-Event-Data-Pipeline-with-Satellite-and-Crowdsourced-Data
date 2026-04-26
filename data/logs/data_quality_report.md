# Data Quality Report — staging.flood_events

_Generated: 2026-04-26T17:09:34+00:00_

## Per-source summary

| Source | Rows | With coords | With H3 | With severity | Earliest | Latest |
|--------|------|-------------|---------|---------------|----------|--------|
| Copernicus_EMS | 335 | 0 | 0 | 0 | 2012-06-11 11:46:00 | 2026-11-03 16:51:00 |
| Dartmouth_FO | 913 | 913 | 913 | 913 | 2000-02-17 00:00:00 | 2018-12-05 00:00:00 |
| EM-DAT | 6178 | 1007 | 1007 | 0 | 1900-01-06 00:00:00 | 2026-03-15 00:00:00 |
| GloFAS | 5503 | 0 | 0 | 5503 | 1985-01-01 00:00:00 | 2024-01-06 00:00:00 |
| ReliefWeb | 1779 | 0 | 0 | 0 | 1984-02-01 00:00:00 | 2026-03-25 00:00:00 |

## Checks

| Check | Failing rows |
|-------|--------------|
| `duplicate_source_event_ids` | 0 (OK) |
| `missing_date_start` | 0 (OK) |
| `invalid_latitude_or_longitude` | 0 (OK) |
| `invalid_geometry` | 0 (OK) |
| `missing_source` | 0 (OK) |
| `missing_h3_with_coords` | 0 (OK) |
| `severity_out_of_range` | 0 (OK) |

