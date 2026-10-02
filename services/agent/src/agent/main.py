from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from nats.aio.msg import Msg
from shared.config import settings
from shared.models.popup import Popup
from shared.rule_engine import RawEventBatch, RuleEngine

from agent.core import bus
from agent.core.rules import load_ruleset
from agent.services.agent_service import AgentManager


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("agent")

# Wildcard over the per-employee subjects the streamer publishes to
# (`events.raw.{employee_id}`): one subscription, state isolated per employee.
SUBJECT_IN = "events.raw.>"
SUBJECT_OUT = "popups.detected"


async def run() -> None:
    logger.info("agent starting")
    ruleset = load_ruleset(settings.RULES_FILE)
    client = await bus.connect(settings.nats_url)

    async def publish(popup: Popup) -> None:
        await client.publish(SUBJECT_OUT, popup.model_dump_json().encode())

    manager = AgentManager(lambda: RuleEngine(ruleset.rules), publish)

    async def handler(msg: Msg) -> None:
        try:
            events = RawEventBatch.validate_json(msg.data)
        except Exception:
            logger.exception("dropping malformed event batch on %s", msg.subject)
            return
        await manager.process_batch(events)

    # No queue group: edge-triggered state is per-employee, so a single consumer keeps
    # each employee's event order and firing state consistent.
    await client.subscribe(SUBJECT_IN, cb=handler)
    logger.info("subscribed to %s, publishing matches to %s", SUBJECT_IN, SUBJECT_OUT)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        logger.info("agent shutting down")
        await client.drain()
        logger.info("agent stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
