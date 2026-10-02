from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable

from shared.models.popup import Popup
from shared.rule_engine import RawEvent, RuleEngine


logger = logging.getLogger("agent.services.agent")

PublishFn = Callable[[Popup], Awaitable[None]]
EngineFactory = Callable[[], RuleEngine]

__all__ = ["AgentManager", "AgentProcessor", "RawEvent"]


class AgentManager:
    """Routes raw events to a per-employee rule engine and publishes popups.

    Edge-triggered firing is stateful, so each `employee_id` gets its own isolated
    `RuleEngine` (a separate state machine), created lazily on first sight. The engine
    factory and publish sink are injected so the chain is testable without NATS.
    """

    def __init__(self, engine_factory: EngineFactory, publish: PublishFn) -> None:
        self._engine_factory = engine_factory
        self._publish = publish
        self._engines: dict[str, RuleEngine] = {}

    def _engine_for(self, employee: str) -> RuleEngine:
        engine = self._engines.get(employee)
        if engine is None:
            engine = self._engine_factory()
            self._engines[employee] = engine
        return engine

    async def process(self, event: RawEvent) -> list[Popup]:
        engine = self._engine_for(event.employee)
        popups = [
            Popup(employee=event.employee, rule=match.rule, ts=event.ts, title=match.title, body=match.body)
            for match in engine.evaluate(event.context)
        ]
        for popup in popups:
            await self._publish(popup)
        if popups:
            logger.info("published %d popup(s) for %s", len(popups), event.employee)
        return popups

    async def process_batch(self, events: Iterable[RawEvent]) -> list[Popup]:
        """Process a batch of raw events (one `events.raw.*` message) in order."""
        published: list[Popup] = []
        for event in events:
            published.extend(await self.process(event))
        return published


# Backwards-compatible alias: the previous processor name.
AgentProcessor = AgentManager
