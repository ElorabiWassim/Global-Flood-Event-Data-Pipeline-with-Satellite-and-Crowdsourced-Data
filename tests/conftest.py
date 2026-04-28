"""
Pytest bootstrap.

Adds the project root to ``sys.path`` so test modules can ``import config``,
``import db.client`` etc. without installing the project as a package.

We also set a dummy ``DATABASE_URL`` before any project module is imported so
``config.settings`` doesn't fail when running unit tests outside Docker /
without a real Supabase ``.env``. The unit tests in this folder are pure
functions and do not touch the database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Provide a harmless DATABASE_URL so config.settings.get_database_url() never
# raises during import. Tests that hit the DB are out of scope for this suite.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://test:test@localhost:5432/test",
)
