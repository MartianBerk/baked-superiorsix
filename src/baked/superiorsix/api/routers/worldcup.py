"""World Cup / Qatar Hero routes (legacy subdomains: worldcup, qatarhero)."""

from typing import Any

from fastapi import APIRouter

from baked.superiorsix.api.deps import RequireQatarHero
from baked.superiorsix.noop import noop

router = APIRouter(tags=["worldcup"])


@router.get("/worldcup/matches")
async def worldcup_matches() -> dict[str, Any]:
    return noop()


@router.get("/worldcup/scores")
async def worldcup_scores() -> dict[str, Any]:
    return noop()


@router.get("/worldcup/listpredictions")
async def worldcup_list_predictions() -> dict[str, Any]:
    return noop()


@router.get("/qatarhero/listpredictions")
async def qatarhero_list_predictions() -> dict[str, Any]:
    """Legacy alternate subdomain alias for listpredictions."""
    return noop()


@router.get("/worldcup/getpredictions")
async def worldcup_get_predictions() -> dict[str, Any]:
    return noop()


@router.get("/worldcup/getprediction")
async def worldcup_get_prediction(_: RequireQatarHero) -> dict[str, Any]:
    return noop()


@router.post("/worldcup/addprediction")
async def worldcup_add_prediction(_: RequireQatarHero) -> dict[str, Any]:
    return noop()
