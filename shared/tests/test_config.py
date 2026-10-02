from __future__ import annotations

from shared.config import Settings


def test_computed_urls_compose_from_atomic_parts() -> None:
    settings = Settings(
        NATS_HOST="nats-host",
        NATS_PORT=4222,
        REDIS_HOST="redis-host",
        REDIS_PORT=6379,
        REDIS_DB=2,
        POSTGRES_USER="user",
        POSTGRES_PASSWORD="pass",
        POSTGRES_HOST="db-host",
        POSTGRES_PORT=5432,
        POSTGRES_DB="contextpath",
    )

    assert settings.nats_url == "nats://nats-host:4222"
    assert settings.redis_url == "redis://redis-host:6379/2"
    assert settings.timescale_dsn == "postgresql://user:pass@db-host:5432/contextpath"
