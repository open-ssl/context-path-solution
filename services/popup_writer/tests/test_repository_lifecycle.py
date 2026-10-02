from __future__ import annotations

import pytest
from popup_writer.db import repository


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
    def __init__(self) -> None:
        self.conn = FakeConn()
        self.closed = False

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)

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
    assert any("CREATE TABLE" in statement for statement in pool.conn.executed)


async def test_init_pool_success_sets_state_and_migrates(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()

    async def fake_create_pool(dsn: str, **kwargs: object) -> FakePool:
        return pool

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)

    result = await repository.init_pool("postgresql://x", retries=1, delay=0)
    assert result is pool
    assert repository.get_pool() is pool
    assert pool.conn.executed  # migrations ran


async def test_init_pool_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}
    pool = FakePool()

    async def flaky_create_pool(dsn: str, **kwargs: object) -> FakePool:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("db not ready")
        return pool

    monkeypatch.setattr("asyncpg.create_pool", flaky_create_pool)

    result = await repository.init_pool("postgresql://x", retries=3, delay=0)
    assert result is pool
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
    await repository.close_pool()  # pool is None -> must not raise
    assert repository._state["pool"] is None
