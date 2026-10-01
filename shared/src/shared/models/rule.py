from __future__ import annotations

from pydantic import BaseModel


# A condition is a single pattern or a list of patterns (list matches if any one matches).
Condition = str | list[str]


class When(BaseModel):
    click: Condition | None = None
    process: Condition | None = None
    window_title: Condition | None = None
    clipboard: Condition | None = None
    ocr: Condition | None = None


class PopupTemplate(BaseModel):
    title: str
    body: str


class Rule(BaseModel):
    id: str
    when: When
    popup: PopupTemplate


class RuleSet(BaseModel):
    rules: list[Rule]
