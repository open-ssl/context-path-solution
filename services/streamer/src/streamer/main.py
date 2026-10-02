from __future__ import annotations

import asyncio
import logging

from shared.config import settings
from shared.rule_engine import RawEvent

from streamer.core import bus
from streamer.services.archive import load_archives
from streamer.services.replay import PublishBatchFn, run_workers


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("streamer")

SUBJECT_PREFIX = "events.raw"


async def run() -> None:
    logger.info("streamer starting")
    archives = load_archives(settings.DATA_DIR)
    if not archives:
        logger.warning("no archives found in %s; nothing to replay", settings.DATA_DIR)
        return

    client = await bus.connect(settings.nats_url)

    def publish_factory(employee: str) -> PublishBatchFn:
        # One isolated topic per employee; the agent subscribes to the `events.raw.>`
        # wildcard and keeps a separate rule-engine state machine per subject.
        subject = f"{SUBJECT_PREFIX}.{employee}"

        async def publish(events: list[RawEvent]) -> None:
            await bus.publish_batch(client, subject, events)

        return publish

    try:
        published = await run_workers(archives, publish_factory, tick_ms=settings.TICK_MS)
        await client.flush()
        logger.info("streamer finished: %d RawEvent(s) published under %s.*", published, SUBJECT_PREFIX)
    finally:
        await client.drain()
        logger.info("streamer stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
