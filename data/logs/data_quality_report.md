# Data Quality Report — staging.flood_events

_Generated: 2026-05-14T03:27:45+00:00_

## Per-source summary

| Source | Rows | With coords | With H3 | With severity | Earliest | Latest |
|--------|------|-------------|---------|---------------|----------|--------|
| Copernicus_EMS | 345 | 0 | 0 | 0 | 2012-07-09 12:56:00 | 2026-04-01 15:16:00 |
| Dartmouth_FO | 4616 | 4616 | 4615 | 4616 | 1985-01-01 00:00:00 | 2018-12-05 00:00:00 |
| Dartmouth_MasterList | 5503 | 0 | 0 | 5503 | 1985-01-01 00:00:00 | 2024-01-06 00:00:00 |
| EM-DAT | 8775 | 1007 | 1007 | 0 | 1900-01-06 00:00:00 | 2026-03-15 00:00:00 |
| ReliefWeb | 1779 | 0 | 0 | 0 | 1984-02-01 00:00:00 | 2026-03-25 00:00:00 |

## Social signal summary

| Platform | Rows | With country | With coords | With H3 | Avg confidence | Earliest | Latest |
|----------|------|--------------|-------------|---------|----------------|----------|--------|
| bluesky | 46 | 28 | 0 | 0 | 0.76625 | 2026-05-13 04:13:27.825494+00:00 | 2026-05-14 03:01:00.485000+00:00 |

## Checks

| Check | Failing rows |
|-------|--------------|
| `duplicate_source_event_ids` | 0 (OK) |
| `missing_date_start` | 0 (OK) |
| `invalid_latitude_or_longitude` | 1 |
| `invalid_geometry` | 0 (OK) |
| `missing_source` | 0 (OK) |
| `missing_h3_with_coords` | 1 |
| `severity_out_of_range` | 1 |
| `social_duplicate_platform_post_ids` | 0 (OK) |
| `social_missing_created_at` | 0 (OK) |
| `social_missing_platform_or_post_id` | 0 (OK) |
| `social_invalid_latitude_or_longitude` | 0 (OK) |
| `social_missing_h3_with_coords` | 0 (OK) |
| `social_confidence_out_of_range` | 0 (OK) |
| `social_relevance_without_keywords` | 0 (OK) |
| `social_orphan_staging_signals` | 0 (OK) |

