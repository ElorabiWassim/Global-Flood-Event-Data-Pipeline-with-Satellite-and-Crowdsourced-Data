from pathlib import Path

from sqlalchemy import create_engine, text

from config.settings import settings


def main() -> None:
    schema_path = Path("shema.sql")
    sql = schema_path.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]

    engine = create_engine(settings.resolved_database_url, pool_pre_ping=True)

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))

    print(f"Applied schema from {schema_path.resolve()} to {settings.postgres_host}")


if __name__ == "__main__":
    main()