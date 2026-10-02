from __future__ import annotations

import logging
from pathlib import Path

from shared.models.rule import RuleSet


logger = logging.getLogger("agent.core.rules")


def load_ruleset(path: str) -> RuleSet:
    ruleset = RuleSet.model_validate_json(Path(path).read_text(encoding="utf-8"))
    logger.info("loaded %d rule(s) from %s", len(ruleset.rules), path)
    return ruleset
