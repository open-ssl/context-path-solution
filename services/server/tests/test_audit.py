from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from server.db import repository
from shared.models.popup import Popup


def _popup(employee: str, rule: str = "forwarded-email-opened") -> Popup:
    return Popup(
        employee=employee,
        rule=rule,
        ts=datetime(2026, 3, 10, 16, 0, 8, tzinfo=timezone.utc),
        title="Forwarded email",
        body="You opened a forwarded email: FW: hi",
    )


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_cache_hit_for_employee_and_rule(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    async def fake(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
        calls.append((employee, rule, limit))
        return [_popup("emp-cr")]

    monkeypatch.setattr(repository, "fetch_audits", fake)

    first = await client.get("/audit", params={"employee": "emp-cr", "rule": "forwarded-email-opened"})
    second = await client.get("/audit", params={"employee": "emp-cr", "rule": "forwarded-email-opened"})

    assert first.status_code == 200
    assert first.json() == second.json()
    # Second identical request is served from the cache.
    assert len(calls) == 1


async def test_cache_hit_for_employee_only(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    async def fake(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
        calls.append((employee, rule, limit))
        return [_popup("emp-only")]

    monkeypatch.setattr(repository, "fetch_audits", fake)

    await client.get("/audit", params={"employee": "emp-only"})
    await client.get("/audit", params={"employee": "emp-only"})

    assert len(calls) == 1
    assert calls[0] == ("emp-only", None, 50)


async def test_unkeyed_query_bypasses_cache(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    async def fake(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
        calls.append((employee, rule, limit))
        return []

    monkeypatch.setattr(repository, "fetch_audits", fake)

    await client.get("/audit")
    await client.get("/audit")

    # No employee -> direct DB each time, never cached.
    assert len(calls) == 2
    assert calls[0][0] is None


async def test_limit_is_forwarded(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, int] = {}

    async def fake(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
        seen["limit"] = limit
        return []

    monkeypatch.setattr(repository, "fetch_audits", fake)

    await client.get("/audit", params={"employee": "emp-lim", "limit": 5})
    assert seen["limit"] == 5


async def test_response_matches_spec_shape(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(employee: str | None, rule: str | None, limit: int) -> list[Popup]:
        return [_popup("emp-schema")]

    monkeypatch.setattr(repository, "fetch_audits", fake)

    response = await client.get("/audit", params={"employee": "emp-schema"})
    body = response.json()

    assert len(body) == 1
    item = body[0]
    assert set(item) == {"employee", "rule", "ts", "title", "body"}
    assert item["employee"] == "emp-schema"
    assert item["ts"] == "2026-03-10T16:00:08Z"
