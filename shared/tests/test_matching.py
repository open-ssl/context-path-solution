from __future__ import annotations

import pytest
from shared.rule_engine.matching import Matcher


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        ("FW:*", "FW: Quarterly report", True),
        ("FW:*", "RE: Quarterly report", False),
        ("*urgent*", "This is URGENT, please read", True),
        ("*urgent*", "nothing to see here", False),
        ("Delete", "delete", True),
        ("Delete", "Delete all", False),
        ("Delete", "Delete", True),
        ("*| Salesforce*", "Lead: Acme | Salesforce", True),
        ("INV-*", "INV-99120", True),
        ("INV-*", "PO-99120", False),
    ],
)
def test_single_pattern(pattern: str, text: str, expected: bool) -> None:
    assert Matcher(pattern).match(text) is expected


def test_list_matches_any() -> None:
    matcher = Matcher(["OUTLOOK.EXE", "olk.exe"])
    assert matcher.match("outlook.exe") is True
    assert matcher.match("OLK.EXE") is True
    assert matcher.match("chrome.exe") is False


def test_wildcard_matches_newlines() -> None:
    assert Matcher("Are you sure*").match("Are you sure\nyou want to delete") is True
