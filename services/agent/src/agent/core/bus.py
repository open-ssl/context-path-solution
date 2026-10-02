from __future__ import annotations

import asyncio
import logging

import nats
from nats.aio.client import Client as NatsClient


logger = logging.getLogger("agent.core.bus")


async def connect(url: str, *, retries: int = 10, delay: float = 1.0) -> NatsClient:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            client = await nats.connect(url)
            logger.info("connected to NATS at %s", url)
            return client
        except Exception as error:  # noqa: BLE001
            last_error = error
            logger.warning("NATS not ready (attempt %d/%d): %s", attempt, retries, error)
            await asyncio.sleep(delay)
    raise RuntimeError("could not connect to NATS") from last_error
