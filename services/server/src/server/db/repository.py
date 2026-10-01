from __future__ import annotations

import asyncio
import logging

import asyncpg
from shared.models.popup import Popup


logger = logging.getLogger("server.db.repository")

_DDL = (
    "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;",
    """
    CREATE TABLE IF NOT EXISTS popups (
        employee TEXT NOT NULL,
        rule     TEXT NOT NULL,
        ts       TIMESTAMPTZ NOT NULL,
        title    TEXT NOT NULL,
        body     TEXT NOT NULL,
        UNIQUE (employee, rule, ts)
    );
    """,
    "SELECT create_hypertable('popups', 'ts', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS popups_emp_rule_ts_idx ON popups (employee, rule, ts DESC);",
)

_FETCH_SQL = """
    SELECT employee, rule, ts, title, body
    FROM popups
    WHERE ($1::text IS NULL OR employee = $1)
      AND ($2::text IS NULL OR rule = $2)
    ORDER BY ts DESC
    LIMIT $3
"""

_state: dict[str, asyncpg.Pool | None] = {"pool": None}


def get_pool() -> asyncpg.Pool:
    pool = _state["pool"]
    if pool is None:
        raise RuntimeError("Database pool is not initialized")
    return pool


async def run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for statement in _DDL:
            await conn.execute(statement)
    logger.info("database schema ready")


async def init_pool(dsn: str, *, retries: int = 10, delay: float = 1.0) -> asyncpg.Pool:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
            await run_migrations(pool)
            _state["pool"] = pool
            logger.info("database pool ready")
            return pool
        except Exception as error:  # noqa: BLE001
            last_error = error
            logger.warning("database not ready (attempt %d/%d): %s", attempt, retries, error)
            await asyncio.sleep(delay)
    raise RuntimeError("could not connect to the database") from last_error


async def close_pool() -> None:
    pool = _state["pool"]
    if pool is not None:
        await pool.close()
        _state["pool"] = None
        logger.info("database pool closed")


async def fetch_audits(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
    rows = await get_pool().fetch(_FETCH_SQL, employee, rule, limit)
    return [Popup.model_validate(dict(row)) for row in rows]
