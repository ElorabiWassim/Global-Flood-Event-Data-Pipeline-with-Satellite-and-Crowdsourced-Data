# check.py
from config.settings import settings
from sqlalchemy import create_engine, text

engine = create_engine(settings.resolved_database_url)
with engine.connect() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM flood_events WHERE source='reliefweb'")).scalar()
    print(f"ReliefWeb rows in Supabase: {count}")