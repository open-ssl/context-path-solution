from __future__ import annotations

from pathlib import Path

from agent.core.rules import load_ruleset


def test_load_ruleset_reads_and_validates(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(
        '{"rules": [{"id": "r1", "when": {"click": "Done"}, "popup": {"title": "t", "body": "b"}}]}',
        encoding="utf-8",
    )

    ruleset = load_ruleset(str(rules_file))

    assert len(ruleset.rules) == 1
    assert ruleset.rules[0].id == "r1"
    assert ruleset.rules[0].when.click == "Done"
