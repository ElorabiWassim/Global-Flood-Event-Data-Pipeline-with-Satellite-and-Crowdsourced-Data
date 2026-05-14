"""Print the 10 lowest-confidence social signals so we can see what filter
rules need to tighten. One-shot helper, safe to delete after step 13."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.client import fetch_all  # noqa: E402

for row in fetch_all(
    """
    SELECT post_id,
           ROUND(signal_confidence::numeric, 3) AS conf,
           ROUND(flood_relevance_score::numeric, 3) AS rel,
           matched_keywords,
           LEFT(text, 200) AS text
    FROM staging.social_flood_signals
    ORDER BY signal_confidence ASC
    LIMIT 15
    """
):
    print(row)
    print()
