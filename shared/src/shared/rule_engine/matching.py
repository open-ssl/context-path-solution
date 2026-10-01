from __future__ import annotations

import re

from shared.models.rule import Condition


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    # Only "*" is special (matches any characters). The pattern must match the whole
    # text, case-insensitively.
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.compile(f"^{regex}$", re.IGNORECASE | re.DOTALL)


class Matcher:
    def __init__(self, condition: Condition) -> None:
        patterns = [condition] if isinstance(condition, str) else list(condition)
        self._patterns = [_compile_pattern(p) for p in patterns]

    def match(self, text: str) -> bool:
        return any(p.match(text) for p in self._patterns)
