"""Public stats routes (legacy subdomain: stats)."""

from typing import Any

from fastapi import APIRouter

from baked_superiorsix.noop import noop

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/aggregate")
async def aggregate_stats() -> dict[str, Any]:
    return noop()


@router.get("/winners")
async def winners() -> dict[str, Any]:
    return noop()
