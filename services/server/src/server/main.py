from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from shared.config import settings

from server.api.audit import router as audit_router
from server.core import cache
from server.db import repository


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await repository.init_pool(settings.timescale_dsn)
    app.state.redis = await cache.init_cache(settings.redis_url)
    logger.info("server started")
    try:
        yield
    finally:
        await cache.close_cache(app.state.redis)
        await repository.close_pool()
        logger.info("server stopped")


app = FastAPI(
    title=settings.title,
    docs_url=settings.docs_url,
    openapi_url=settings.openapi_url,
    lifespan=lifespan,
)
app.include_router(audit_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("server.main:app", host="0.0.0.0", port=settings.SERVER_PORT)


if __name__ == "__main__":
    main()
