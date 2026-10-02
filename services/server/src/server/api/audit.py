from __future__ import annotations

from fastapi import APIRouter
from shared.models.popup import Popup

from server.services.audit_service import get_audits


router = APIRouter()


@router.get("/audit", response_model=list[Popup])
async def audit(
    employee: str | None = None,
    rule: str | None = None,
) -> list[Popup]:
    return await get_audits(employee=employee, rule=rule)
