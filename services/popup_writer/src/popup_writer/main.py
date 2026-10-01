from __future__ import annotations

import asyncio
import logging


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("popup_writer")


async def run() -> None:
    logger.info("popup_writer starting")
    # Stage 4: subscribe to popups.detected, dual-write Redis cache + TimescaleDB batches.
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
