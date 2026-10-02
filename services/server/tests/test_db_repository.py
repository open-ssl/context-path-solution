from __future__ import annotations

from datetime import UTC, datetime

import pytest
from server.db import repository


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, statement: str) -> None:
        self.executed.append(statement)


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePool:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.conn = FakeConn()
        self.closed = False
        self.rows = rows or []
        self.fetch_args: tuple = ()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.fetch_args = args
        return self.rows

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(repository._state, "pool", None)


def test_get_pool_raises_when_uninitialized() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        repository.get_pool()


async def test_run_migrations_executes_all_ddl() -> None:
    pool = FakePool()
    await repository.run_migrations(pool)
    assert len(pool.conn.executed) == len(repository._DDL)
    assert any("create_hypertable" in statement for statement in pool.conn.executed)


async def test_init_pool_success(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()

    async def fake_create_pool(dsn: str, **kwargs: object) -> FakePool:
        return pool

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)

    result = await repository.init_pool("postgresql://x", retries=1, delay=0)
    assert result is pool
    assert repository.get_pool() is pool


async def test_init_pool_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}
    pool = FakePool()

    async def flaky(dsn: str, **kwargs: object) -> FakePool:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("not ready")
        return pool

    monkeypatch.setattr("asyncpg.create_pool", flaky)

    assert await repository.init_pool("postgresql://x", retries=3, delay=0) is pool
    assert attempts["n"] == 2


async def test_init_pool_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_fail(dsn: str, **kwargs: object) -> FakePool:
        raise OSError("down")

    monkeypatch.setattr("asyncpg.create_pool", always_fail)

    with pytest.raises(RuntimeError, match="could not connect"):
        await repository.init_pool("postgresql://x", retries=2, delay=0)


async def test_close_pool_closes_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()
    monkeypatch.setitem(repository._state, "pool", pool)
    await repository.close_pool()
    assert pool.closed is True
    assert repository._state["pool"] is None


async def test_close_pool_noop_when_absent() -> None:
    await repository.close_pool()
    assert repository._state["pool"] is None


async def test_fetch_audits_maps_rows_and_forwards_args(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "employee": "emp-1",
            "rule": "r1",
            "ts": datetime(2026, 3, 10, 16, 0, 8, tzinfo=UTC),
            "title": "t",
            "body": "b",
        }
    ]
    pool = FakePool(rows=rows)
    monkeypatch.setitem(repository._state, "pool", pool)

    result = await repository.fetch_audits(employee="emp-1", rule="r1")

    assert len(result) == 1
    assert result[0].employee == "emp-1"
    assert result[0].rule == "r1"
    assert pool.fetch_args == ("emp-1", "r1")
