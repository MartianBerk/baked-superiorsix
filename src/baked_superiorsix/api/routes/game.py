"""Public game routes (legacy subdomain: game)."""

from typing import Any

from fastapi import APIRouter

from baked_superiorsix.noop import noop

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/meta")
async def game_meta() -> dict[str, Any]:
    return noop()


@router.get("/livematches")
async def game_live_matches() -> dict[str, Any]:
    return noop()


@router.get("/livescores")
async def game_live_scores() -> dict[str, Any]:
    return noop()


@router.get("/currentround")
async def game_current_round() -> dict[str, Any]:
    return noop()


@router.get("/specialmessage")
async def game_special_message() -> dict[str, Any]:
    return noop()


@router.get("/matchdetail")
async def game_match_detail() -> dict[str, Any]:
    return noop()
