from __future__ import annotations

import pytest
from shared.models.rule import RuleSet
from shared.rule_engine.engine import MatchContext, RuleEngine


RULES = {
    "rules": [
        {
            "id": "forwarded-email-opened",
            "when": {
                "click": "FW:*",
                "process": ["OUTLOOK.EXE", "olk.exe"],
                "window_title": None,
                "clipboard": None,
                "ocr": None,
            },
            "popup": {"title": "Forwarded email", "body": "You opened a forwarded email: {click}"},
        },
        {
            "id": "urgent-email-opened",
            "when": {"click": "*urgent*", "process": ["OUTLOOK.EXE", "olk.exe"]},
            "popup": {"title": "Urgent email", "body": "{click}"},
        },
        {
            "id": "salesforce-record-deleted",
            "when": {"click": "Delete", "window_title": "*| Salesforce*", "ocr": "Are you sure you want to delete*"},
            "popup": {"title": "Record deleted", "body": "Deleted in Salesforce: {window_title}"},
        },
        {
            "id": "jira-ticket-done",
            "when": {"click": "Done", "window_title": "* - Jira - *", "process": None},
            "popup": {"title": "Ticket moved to Done", "body": "{window_title}"},
        },
        {
            "id": "invoice-ready-to-attach",
            "when": {"clipboard": "INV-*", "process": ["OUTLOOK.EXE", "olk.exe"]},
            "popup": {
                "title": "Invoice in clipboard",
                "body": "You copied {clipboard}. Attach the invoice to this email?",
            },
        },
    ]
}


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine(RuleSet.model_validate(RULES).rules)


def test_forwarded_email_fires_with_substitution(engine: RuleEngine) -> None:
    matches = engine.evaluate(MatchContext(click="FW: Q3 numbers", process="olk.exe"))
    assert len(matches) == 1
    assert matches[0].rule == "forwarded-email-opened"
    assert matches[0].body == "You opened a forwarded email: FW: Q3 numbers"


def test_process_condition_must_match(engine: RuleEngine) -> None:
    assert engine.evaluate(MatchContext(click="FW: hi", process="chrome.exe")) == []


def test_missing_condition_is_ignored(engine: RuleEngine) -> None:
    # jira rule has process=None; it must fire regardless of the process value.
    matches = engine.evaluate(MatchContext(click="Done", window_title="PROJ-1 - Jira - Chrome", process="chrome.exe"))
    assert [m.rule for m in matches] == ["jira-ticket-done"]
    assert matches[0].body == "PROJ-1 - Jira - Chrome"


def test_ocr_list_condition(engine: RuleEngine) -> None:
    ctx = MatchContext(
        click="Delete",
        window_title="Acme Lead | Salesforce",
        ocr=["random text", "Are you sure you want to delete this record?"],
    )
    matches = engine.evaluate(ctx)
    assert [m.rule for m in matches] == ["salesforce-record-deleted"]
    assert matches[0].body == "Deleted in Salesforce: Acme Lead | Salesforce"


def test_clipboard_trigger(engine: RuleEngine) -> None:
    matches = engine.evaluate(MatchContext(clipboard="INV-99120", process="OUTLOOK.EXE"))
    assert matches[0].rule == "invoice-ready-to-attach"
    assert matches[0].body == "You copied INV-99120. Attach the invoice to this email?"


def test_edge_triggered_does_not_refire_while_satisfied(engine: RuleEngine) -> None:
    ctx = MatchContext(click="FW: hi", process="olk.exe")
    assert len(engine.evaluate(ctx)) == 1
    # Same satisfied state on the next tick must not fire again.
    assert engine.evaluate(ctx) == []


def test_edge_triggered_rearms_after_condition_clears(engine: RuleEngine) -> None:
    fired = MatchContext(click="FW: hi", process="olk.exe")
    idle = MatchContext(process="olk.exe")
    assert len(engine.evaluate(fired)) == 1
    assert engine.evaluate(idle) == []
    # Transition back to satisfied fires once more.
    assert len(engine.evaluate(fired)) == 1


def test_reset_clears_edge_state(engine: RuleEngine) -> None:
    ctx = MatchContext(click="FW: hi", process="olk.exe")
    assert len(engine.evaluate(ctx)) == 1
    engine.reset()
    assert len(engine.evaluate(ctx)) == 1
