from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI


app = FastAPI(title="ContextPath Audit Server")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/audit")
async def audit(employee: str | None = None, rule: str | None = None) -> list[dict]:
    # Stage 3: read from Redis cache with TimescaleDB fallback.
    return []


def main() -> None:
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=int(os.getenv("SERVER_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
