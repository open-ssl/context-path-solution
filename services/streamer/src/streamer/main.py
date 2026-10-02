from __future__ import annotations

import asyncio
import logging

from shared.config import settings
from shared.rule_engine import RawEvent

from streamer.core import bus
from streamer.services.archive import load_archives
from streamer.services.replay import replay


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("streamer")

SUBJECT_OUT = "events.raw"


async def run() -> None:
    logger.info("streamer starting")
    archives = load_archives(settings.DATA_DIR)
    if not archives:
        logger.warning("no archives found in %s; nothing to replay", settings.DATA_DIR)
        return

    client = await bus.connect(settings.nats_url)

    async def publish(event: RawEvent) -> None:
        await client.publish(SUBJECT_OUT, event.model_dump_json().encode())

    try:
        published = await replay(archives, publish, tick_ms=settings.TICK_MS)
        logger.info("streamer finished: %d RawEvent(s) published to %s", published, SUBJECT_OUT)
    finally:
        await client.drain()
        logger.info("streamer stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
