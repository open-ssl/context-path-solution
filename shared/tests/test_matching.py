from __future__ import annotations

import random
import string

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


# --- OCR infix matching (phrase anywhere, whitespace-normalized) ---


def test_infix_anchored_difference() -> None:
    # Anchored (default) requires whole-string; infix finds the phrase anywhere.
    assert Matcher("Delete").match("Please Delete now") is False
    assert Matcher("Delete", infix=True).match("Please Delete now") is True


@pytest.mark.parametrize(
    "text",
    [
        "Are you sure you want to delete this record?",
        "Dialog\nAre you sure you want to delete this record?\nCancel  OK",
        "WARNING   Are you sure you want to delete   this record?",
        "are YOU sure you WANT to DELETE this record",
        "header\n\n\tAre you sure you want to delete\n\tthis account?",
    ],
)
def test_ocr_phrase_found_regardless_of_surrounding_noise(text: str) -> None:
    # The salesforce-style rule pattern with no leading/trailing wildcard must still
    # match when the phrase is buried in multiline, mixed-case, padded OCR text.
    assert Matcher("Are you sure you want to delete*", infix=True).match(text) is True


def test_infix_normalizes_newlines_splitting_a_phrase() -> None:
    # A phrase split across OCR blocks (joined by newlines) still matches after
    # whitespace normalization.
    assert Matcher("want to delete this account", infix=True).match("want to delete\nthis account") is True


def test_infix_absent_phrase_does_not_match() -> None:
    assert Matcher("Are you sure*", infix=True).match("completely unrelated screen text") is False


def test_infix_matching_survives_random_noise() -> None:
    phrase = "Are you sure you want to delete"
    matcher = Matcher(f"{phrase}*", infix=True)
    rng = random.Random(1234)
    alphabet = string.ascii_letters + string.digits + " \n\t.,!?"
    for _ in range(100):
        prefix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        suffix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        noisy = f"{prefix}{phrase} this item{suffix}"
        assert matcher.match(noisy) is True
