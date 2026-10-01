from __future__ import annotations

from datetime import UTC, datetime

import pytest
from popup_writer.db import repository
from shared.models.popup import Popup


def make_popup(n: int) -> Popup:
    return Popup(
        employee="emp-1",
        rule=f"rule-{n}",
        ts=datetime(2026, 3, 10, 16, 0, n, tzinfo=UTC),
        title="t",
        body="b",
    )


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple]]] = []

    async def executemany(self, sql: str, records: list[tuple]) -> None:
        self.calls.append((sql, records))


class FakeAcquire:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConnection()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    pool = FakePool()
    monkeypatch.setitem(repository._state, "pool", pool)
    return pool


async def test_insert_popups_maps_rows_in_order(fake_pool: FakePool) -> None:
    await repository.insert_popups([make_popup(1), make_popup(2)])

    assert len(fake_pool.conn.calls) == 1
    _sql, records = fake_pool.conn.calls[0]
    assert records == [
        ("emp-1", "rule-1", make_popup(1).ts, "t", "b"),
        ("emp-1", "rule-2", make_popup(2).ts, "t", "b"),
    ]


async def test_insert_popups_empty_is_noop(fake_pool: FakePool) -> None:
    await repository.insert_popups([])
    assert fake_pool.conn.calls == []
