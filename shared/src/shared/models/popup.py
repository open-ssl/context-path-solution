from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Popup(BaseModel):
    employee: str
    rule: str
    # ts is the time of the event that triggered the popup.
    ts: datetime
    title: str
    body: str


class AuditQuery(BaseModel):
    employee: str | None = None
    rule: str | None = None
