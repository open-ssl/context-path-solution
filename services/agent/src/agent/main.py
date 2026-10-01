from __future__ import annotations

import asyncio
import logging


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("agent")


async def run() -> None:
    logger.info("agent starting")
    # Stage 5: load rules.json, subscribe to ticks.*, evaluate rules, publish popups.detected.
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
