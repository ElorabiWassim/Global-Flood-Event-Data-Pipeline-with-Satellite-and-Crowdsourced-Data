from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_db:       str        = "flood_pipeline"
    postgres_user:     str | None = None
    postgres_password: str | None = None
    postgres_host:     str        = "localhost"
    postgres_port:     int        = 5432
    database_url:      str | None = None
    api_host:          str        = "0.0.0.0"
    api_port:          int        = 8000
    h3_resolution:     int        = 7
    reliefweb_appname: str        = "ai-students-flood-project-mkh59"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @computed_field
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if not self.postgres_user or not self.postgres_password:
            raise ValueError(
                "Set DATABASE_URL or both POSTGRES_USER and POSTGRES_PASSWORD in environment."
            )
        return (
            "postgresql+psycopg2://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()