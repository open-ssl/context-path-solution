from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Data Sources & App Config
    DATA_DIR: str = "./data"
    RULES_FILE: str = "./rules.json"
    TICK_MS: int = 25
    SERVER_PORT: int = 8080

    # popup_writer batching: flush on size threshold or elapsed interval, whichever first.
    WRITER_BATCH_SIZE: int = 100
    WRITER_FLUSH_INTERVAL: float = 5.0

    # API documentation
    DOCS_URL: str = "/api"
    OPENAPI_URL: str = "/api/openapi.json"
    TITLE: str = "Activity Monitoring Read API"

    # NATS
    NATS_HOST: str = "nats"
    NATS_PORT: int = 4222

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # Postgres / TimescaleDB
    POSTGRES_HOST: str = "timescaledb"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "contextpath"
    POSTGRES_PASSWORD: str = "contextpath"
    POSTGRES_DB: str = "contextpath"

    @computed_field
    @property
    def nats_url(self) -> str:
        return f"nats://{self.NATS_HOST}:{self.NATS_PORT}"

    @computed_field
    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @computed_field
    @property
    def timescale_dsn(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def docs_url(self) -> str:
        return self.DOCS_URL

    @computed_field
    @property
    def openapi_url(self) -> str:
        return self.OPENAPI_URL

    @computed_field
    @property
    def title(self) -> str:
        return self.TITLE


settings = Settings()
