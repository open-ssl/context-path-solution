from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

from shared.models.rule import Rule
from shared.rule_engine.matching import Matcher


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class MatchContext(BaseModel):
    click: str | None = None
    clipboard: str | None = None
    window_title: str | None = None
    process: str | None = None
    ocr: list[str] = []


class RuleMatch(BaseModel):
    rule: str
    title: str
    body: str


def _render(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


class _CompiledRule:
    def __init__(self, rule: Rule) -> None:
        self.rule = rule
        self.matchers: dict[str, Matcher] = {
            field: Matcher(condition) for field, condition in rule.when.model_dump().items() if condition is not None
        }

    def match(self, ctx: MatchContext) -> dict[str, str] | None:
        matched: dict[str, str] = {}
        for field, matcher in self.matchers.items():
            if field == "ocr":
                block = next((text for text in ctx.ocr if matcher.match(text)), None)
                if block is None:
                    return None
                matched["ocr"] = block
                continue
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
