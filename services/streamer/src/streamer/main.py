from __future__ import annotations

import asyncio
import logging


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("streamer")


async def run() -> None:
    logger.info("streamer starting")
    # Stage 6: discover /data/*.tar.gz, stream one task per archive, k-way merge by ts,
    # pace one-second ticks by TICK_MS, and publish raw events to events.raw.
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
