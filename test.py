from sqlalchemy import create_engine, text
from config.settings import settings

engine = create_engine(settings.resolved_database_url, pool_pre_ping=True)

with engine.connect() as conn:
    result = conn.execute(text("select count(*) from flood_events where source = 'emdat'"))
    print("EM-DAT rows:", result.scalar())
