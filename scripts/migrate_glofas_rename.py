"""One-time migration:
  1. Rename 'GloFAS' -> 'Dartmouth_MasterList' in staging.flood_events.
  2. Refresh marts (creates flood_events_unique + rebuilds rollups).

Idempotent: re-running after the rename is a no-op for step 1 because the
WHERE clause matches no rows. Step 2 always replaces views in place.
"""
from __future__ import annotations

from sqlalchemy import text

from db.client import get_engine
from transformations.marts import refresh_marts

OLD_NAME = "GloFAS"
NEW_NAME = "Dartmouth_MasterList"


def main() -> None:
    eng = get_engine()
    with eng.begin() as conn:
        before = conn.execute(text(
            "SELECT COUNT(*) FROM staging.flood_events WHERE source = :s"
        ), {"s": OLD_NAME}).scalar() or 0
        print(f"Rows with source='{OLD_NAME}' before migration: {before:,}")

        if before > 0:
            updated = conn.execute(text(
                "UPDATE staging.flood_events SET source = :new "
                "WHERE source = :old"
            ), {"old": OLD_NAME, "new": NEW_NAME}).rowcount
            print(f"  -> renamed to '{NEW_NAME}': {updated:,} rows")
        else:
            print(f"  -> nothing to rename (source already '{NEW_NAME}'?)")

        after_old = conn.execute(text(
            "SELECT COUNT(*) FROM staging.flood_events WHERE source = :s"
        ), {"s": OLD_NAME}).scalar() or 0
        after_new = conn.execute(text(
            "SELECT COUNT(*) FROM staging.flood_events WHERE source = :s"
        ), {"s": NEW_NAME}).scalar() or 0
        print(f"After: '{OLD_NAME}'={after_old}  '{NEW_NAME}'={after_new}")

    print()
    print("Refreshing marts schema (adds flood_events_unique + rollups)...")
    refresh_marts()
    print("Done.")

    # Final sanity check.
    with eng.connect() as conn:
        n_raw = conn.execute(text("SELECT COUNT(*) FROM marts.flood_events")).scalar()
        n_uniq = conn.execute(text("SELECT COUNT(*) FROM marts.flood_events_unique")).scalar()
        d_raw = conn.execute(text("SELECT COALESCE(SUM(displaced),0) FROM marts.flood_events")).scalar()
        d_uniq = conn.execute(text("SELECT COALESCE(SUM(displaced),0) FROM marts.flood_events_unique")).scalar()
    print()
    print(f"marts.flood_events           rows={n_raw:,}   total_displaced={int(d_raw):,}")
    print(f"marts.flood_events_unique    rows={n_uniq:,}   total_displaced={int(d_uniq):,}")
    print(f"  collapsed                  rows={n_raw - n_uniq:,}  displaced removed={int(d_raw - d_uniq):,}")


if __name__ == "__main__":
    main()
