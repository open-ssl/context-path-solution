from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from nats.aio.msg import Msg
from shared.config import settings
from shared.models.popup import Popup

from popup_writer.core import bus
from popup_writer.db import repository
from popup_writer.services.writer_service import BatchWriter


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("popup_writer")

SUBJECT = "popups.detected"
QUEUE_GROUP = "popup-writers"


async def run() -> None:
    logger.info("popup_writer starting")
    await repository.init_pool(settings.timescale_dsn)
    writer = BatchWriter(
        repository.insert_popups,
        batch_size=settings.WRITER_BATCH_SIZE,
        flush_interval=settings.WRITER_FLUSH_INTERVAL,
    )
    writer.start()

    async def handler(msg: Msg) -> None:
        try:
            popup = Popup.model_validate_json(msg.data)
        except Exception:
            logger.exception("dropping malformed popup message")
            return
        await writer.add(popup)

    client = await bus.connect(settings.nats_url)
    await client.subscribe(SUBJECT, queue=QUEUE_GROUP, cb=handler)
    logger.info("subscribed to %s (queue=%s)", SUBJECT, QUEUE_GROUP)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        logger.info("popup_writer shutting down")
        await client.drain()
        await writer.stop()
        await repository.close_pool()
        logger.info("popup_writer stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
