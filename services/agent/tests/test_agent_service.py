from __future__ import annotations

from datetime import UTC, datetime

from agent.services.agent_service import AgentManager, AgentProcessor, RawEvent
from shared.models.popup import Popup
from shared.models.rule import RuleSet
from shared.rule_engine import MatchContext, RuleEngine


RULES = {
    "rules": [
        {
            "id": "forwarded-email-opened",
            "when": {"click": "FW:*", "process": ["OUTLOOK.EXE", "olk.exe"]},
            "popup": {"title": "Forwarded email", "body": "You opened a forwarded email: {click}"},
        }
    ]
}

EXTENDED_RULES = {
    "rules": [
        {
            "id": "salesforce-record-deleted",
            "when": {
                "click": "Delete",
                "window_title": "*| Salesforce*",
                "ocr": "Are you sure you want to delete*",
            },
            "popup": {
                "title": "Record deleted",
                "body": "Deleted in Salesforce: {window_title}",
            },
        },
        {
            "id": "invoice-ready-to-attach",
            "when": {
                "clipboard": "INV-*",
                "process": ["OUTLOOK.EXE", "olk.exe"],
            },
            "popup": {
                "title": "Invoice in clipboard",
                "body": "You copied {clipboard}. Attach the invoice to this email?",
            },
        },
    ]
}


TS = datetime(2026, 3, 10, 16, 0, 8, tzinfo=UTC)


class Collector:
    def __init__(self) -> None:
        self.published: list[Popup] = []

    async def __call__(self, popup: Popup) -> None:
        self.published.append(popup)


def make_processor(collector: Collector) -> AgentProcessor:
    ruleset = RuleSet.model_validate(RULES)
    return AgentProcessor(lambda: RuleEngine(ruleset.rules), collector)


def make_extended_processor(collector: Collector) -> AgentProcessor:
    ruleset = RuleSet.model_validate(EXTENDED_RULES)
    return AgentProcessor(lambda: RuleEngine(ruleset.rules), collector)


def event(employee: str, *, click: str | None, process: str | None, ts: datetime = TS) -> RawEvent:
    return RawEvent(employee=employee, ts=ts, context=MatchContext(click=click, process=process))


async def test_match_publishes_popup_with_substitution() -> None:
    collector = Collector()
    processor = make_processor(collector)

    popups = await processor.process(event("emp-1", click="FW: Q3 numbers", process="olk.exe"))

    assert len(collector.published) == 1
    published = collector.published[0]
    assert published.employee == "emp-1"
    assert published.rule == "forwarded-email-opened"
    assert published.ts == TS
    assert published.title == "Forwarded email"
    assert published.body == "You opened a forwarded email: FW: Q3 numbers"
    assert popups == collector.published


async def test_non_match_publishes_nothing() -> None:
    collector = Collector()
    processor = make_processor(collector)

    popups = await processor.process(event("emp-1", click="FW: hi", process="chrome.exe"))

    assert popups == []
    assert collector.published == []


async def test_edge_triggered_does_not_refire_for_same_employee() -> None:
    collector = Collector()
    processor = make_processor(collector)

    await processor.process(event("emp-1", click="FW: hi", process="olk.exe"))
    await processor.process(event("emp-1", click="FW: hi", process="olk.exe"))

    assert len(collector.published) == 1  # second identical tick is suppressed


async def test_per_employee_state_is_isolated() -> None:
    collector = Collector()
    processor = make_processor(collector)

    await processor.process(event("emp-1", click="FW: hi", process="olk.exe"))
    await processor.process(event("emp-2", click="FW: hi", process="olk.exe"))

    assert [p.employee for p in collector.published] == ["emp-1", "emp-2"]


async def test_case_insensitive_matching() -> None:
    """Case-insensitive matching (Delete == delete, OUTLOOK.EXE == outlook.exe)."""
    collector = Collector()
    processor = make_processor(collector)

    # The rule declares OUTLOOK.EXE and FW:*, but we pass lowercase values.
    e = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(click="fw: urgent report", process="outlook.exe"),
    )
    popups = await processor.process(e)

    assert len(popups) == 1
    assert popups[0].rule == "forwarded-email-opened"


async def test_null_and_missing_fields_are_ignored() -> None:
    """Conditions set to null or missing from the event do not block a match."""
    collector = Collector()
    processor = make_processor(collector)

    # window_title, clipboard, ocr are passed as None — the rule should still fire.
    e = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(
            click="FW: Invoice",
            process="olk.exe",
            window_title=None,
            clipboard=None,
            ocr=None,
        ),
    )
    popups = await processor.process(e)

    assert len(popups) == 1


async def test_list_matching_or_logic() -> None:
    """List matching with OR logic (process: ["OUTLOOK.EXE", "olk.exe"])."""
    collector = Collector()
    processor = make_processor(collector)

    # Check the second process in the list ("olk.exe").
    e1 = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(click="FW: test", process="olk.exe"),
    )
    # Check the first process in the list ("OUTLOOK.EXE").
    e2 = RawEvent(
        employee="emp-2",
        ts=TS,
        context=MatchContext(click="FW: test", process="OUTLOOK.EXE"),
    )

    await processor.process(e1)
    await processor.process(e2)

    assert len(collector.published) == 2


async def test_complex_wildcard_matching() -> None:
    """Complex wildcard (*) patterns in the middle and at the end of the string."""
    collector = Collector()
    processor = make_extended_processor(collector)

    e = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(
            click="Delete",
            window_title="Account Details | Salesforce Lightning",
            ocr="Are you sure you want to delete this account?",
        ),
    )
    popups = await processor.process(e)

    assert len(popups) == 1
    assert popups[0].rule == "salesforce-record-deleted"
    assert popups[0].body == "Deleted in Salesforce: Account Details | Salesforce Lightning"


async def test_partial_match_fails_when_all_conditions_required() -> None:
    """If at least one required condition in 'when' does not match, no popup is created."""
    collector = Collector()
    processor = make_extended_processor(collector)

    # click and window_title match, but OCR contains different text.
    e = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(
            click="Delete",
            window_title="Account Details | Salesforce",
            ocr="Click button to continue",  # Does not match "Are you sure..."
        ),
    )
    popups = await processor.process(e)

    assert popups == []
    assert collector.published == []


async def test_process_batch_routes_per_employee_in_order() -> None:
    """A single `events.raw.*` batch is processed per-event, with isolated state."""
    collector = Collector()
    manager = AgentManager(lambda: RuleEngine(RuleSet.model_validate(RULES).rules), collector)

    batch = [
        event("emp-1", click="FW: a", process="olk.exe"),
        event("emp-1", click="FW: a", process="olk.exe"),  # same employee -> edge-suppressed
        event("emp-2", click="FW: a", process="olk.exe"),  # isolated engine -> fires
    ]
    popups = await manager.process_batch(batch)

    assert [p.employee for p in popups] == ["emp-1", "emp-2"]


async def test_multiple_rules_triggered_in_single_event() -> None:
    """A single event can trigger multiple rules at once when their conditions match."""
    # Build a rule set where 2 rules can fire simultaneously.
    multi_rules = {
        "rules": [
            {
                "id": "rule-1",
                "when": {"process": "olk.exe"},
                "popup": {"title": "P1", "body": "Outlook active"},
            },
            {
                "id": "rule-2",
                "when": {"click": "FW:*"},
                "popup": {"title": "P2", "body": "Forward clicked"},
            },
        ]
    }
    ruleset = RuleSet.model_validate(multi_rules)
    collector = Collector()
    processor = AgentProcessor(lambda: RuleEngine(ruleset.rules), collector)

    e = RawEvent(
        employee="emp-1",
        ts=TS,
        context=MatchContext(click="FW: test", process="olk.exe"),
    )
    popups = await processor.process(e)

    assert len(popups) == 2
    rule_ids = {p.rule for p in popups}
    assert rule_ids == {"rule-1", "rule-2"}
