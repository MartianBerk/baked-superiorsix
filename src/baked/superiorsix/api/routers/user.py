"""Authenticated player routes (legacy subdomain: user)."""

from typing import Any

from fastapi import APIRouter

from baked.superiorsix.api.deps import RequireDetails, RequirePlayer
from baked.superiorsix.noop import noop

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/getprediction")
async def get_prediction(_: RequirePlayer) -> dict[str, Any]:
    return noop()


@router.post("/addprediction")
async def add_prediction(_: RequirePlayer) -> dict[str, Any]:
    return noop()


@router.post("/updatedetails")
async def update_details(_: RequireDetails) -> dict[str, Any]:
    return noop()
