from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from shared.models.popup import Popup
from shared.rule_engine import RawEvent, RuleEngine


logger = logging.getLogger("agent.services.agent")

PublishFn = Callable[[Popup], Awaitable[None]]
EngineFactory = Callable[[], RuleEngine]

__all__ = ["AgentProcessor", "RawEvent"]


class AgentProcessor:
    """Runs raw events through a per-employee rule engine and publishes popups.

    Edge-triggered firing is stateful, so each employee gets an isolated engine;
    the engine factory and publish sink are injected to keep the chain testable
    without NATS.
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
