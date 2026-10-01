from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Display(BaseModel):
    id: int
    bounds: tuple[int, int, int, int]
    scale: float = 1.0
    primary: bool = False


class Machine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hostname: str | None = None
    os_version: str | None = None
    displays: list[Display] = []


class Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str | None = None
    session_id: str
    employee_id: str
    started_at: datetime
    ended_at: datetime
    machine: Machine
