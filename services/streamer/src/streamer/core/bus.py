from __future__ import annotations

import asyncio
import logging

import nats
from nats.aio.client import Client as NatsClient
from shared.rule_engine import RawEvent, RawEventBatch


logger = logging.getLogger("streamer.core.bus")


def encode_batch(events: list[RawEvent]) -> bytes:
    """Serialize an interval's RawEvents into a single NATS payload (a JSON array)."""
    return RawEventBatch.dump_json(events)


async def publish_batch(client: NatsClient, subject: str, events: list[RawEvent]) -> None:
    """Publish a whole batch as one message, avoiding a round-trip per event."""
    await client.publish(subject, encode_batch(events))


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
