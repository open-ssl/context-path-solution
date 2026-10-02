from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, TypeAdapter

from shared.models.rule import Rule
from shared.rule_engine.matching import Matcher


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class MatchContext(BaseModel):
    click: str | None = None
    process: str | list[str] | None = None
    window_title: str | None = None
    clipboard: str | None = None
    ocr: str | None = None


class RawEvent(BaseModel):
    """The `events.raw` wire contract: a rule-engine context tagged with its origin.

    Produced by `streamer` from recorded archives and consumed by `agent`.
    """

    employee: str
    ts: datetime
    context: MatchContext


class RuleMatch(BaseModel):
    rule: str
    title: str
    body: str


# Wire codec for batched `events.raw.*` publishes: the streamer accumulates events
# per interval and ships them as one NATS message (a JSON array) to avoid per-event
# round-trips; the agent decodes the same array. Defined once here so producer and
# consumer share a single contract.
RawEventBatch = TypeAdapter(list[RawEvent])


def _render(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


class _CompiledRule:
    def __init__(self, rule: Rule) -> None:
        self.rule = rule
        # OCR text is matched infix (phrase anywhere, whitespace-normalized); the other
        # fields keep exact whole-string matching.
        self.matchers: dict[str, Matcher] = {
            field: Matcher(condition, infix=(field == "ocr"))
            for field, condition in rule.when.model_dump().items()
            if condition is not None
        }

    def match(self, ctx: MatchContext) -> dict[str, str] | None:
        matched: dict[str, str] = {}
        for field, matcher in self.matchers.items():
            candidate = getattr(ctx, field)
            if candidate is None or not matcher.match(candidate):
                return None
            matched[field] = candidate
        return matched


class RuleEngine:
    def __init__(self, rules: Iterable[Rule]) -> None:
        self._rules = [_CompiledRule(rule) for rule in rules]
        self._satisfied: dict[str, bool] = {}

    def reset(self) -> None:
        self._satisfied.clear()

    def evaluate(self, ctx: MatchContext) -> list[RuleMatch]:
        matches: list[RuleMatch] = []
        for compiled in self._rules:
            values = compiled.match(ctx)
            is_satisfied = values is not None
            # Edge-triggered: fire only on the transition from not-satisfied to satisfied.
            if is_satisfied and not self._satisfied.get(compiled.rule.id, False):
                popup = compiled.rule.popup
                matches.append(
                    RuleMatch(
                        rule=compiled.rule.id,
                        title=_render(popup.title, values),
                        body=_render(popup.body, values),
                    )
                )
            self._satisfied[compiled.rule.id] = is_satisfied
        return matches
