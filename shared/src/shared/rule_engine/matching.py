from __future__ import annotations

import re

from shared.models.rule import Condition


_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    # Collapse newlines and runs of whitespace so a phrase stays matchable no matter
    # how OCR split it across lines/blocks or padded it with spaces.
    return _WHITESPACE.sub(" ", text).strip()


def _compile_pattern(pattern: str, *, infix: bool) -> re.Pattern[str]:
    # Only "*" is special (matches any characters), case-insensitive. Anchored patterns
    # must match the whole text; infix patterns (used for OCR) may match anywhere.
    core = ".*".join(re.escape(part) for part in pattern.split("*"))
    regex = core if infix else f"^{core}$"
    return re.compile(regex, re.IGNORECASE | re.DOTALL)


class Matcher:
    def __init__(self, condition: Condition, *, infix: bool = False) -> None:
        patterns = [condition] if isinstance(condition, str) else list(condition)
        self._infix = infix
        self._patterns = [_compile_pattern(p, infix=infix) for p in patterns]

    def match(self, text: str) -> bool:
        if self._infix:
            normalized = _normalize(text)
            return any(p.search(normalized) for p in self._patterns)
        return any(p.match(text) for p in self._patterns)
