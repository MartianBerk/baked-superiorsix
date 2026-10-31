"""Euros / Euro Wizard routes (legacy subdomains: euros, eurowizard)."""

from typing import Any

from fastapi import APIRouter

from baked.superiorsix.api.deps import RequireEuroWizard
from baked.superiorsix.noop import noop

router = APIRouter(tags=["euros"])


@router.get("/euros/matches")
async def euros_matches() -> dict[str, Any]:
    return noop()


@router.get("/euros/scores")
async def euros_scores() -> dict[str, Any]:
    return noop()


@router.get("/euros/listpredictions")
async def euros_list_predictions() -> dict[str, Any]:
    return noop()


@router.get("/eurowizard/listpredictions")
async def eurowizard_list_predictions() -> dict[str, Any]:
    """Legacy alternate subdomain alias for listpredictions."""
    return noop()


@router.get("/euros/getpredictions")
async def euros_get_predictions() -> dict[str, Any]:
    return noop()


@router.get("/euros/getprediction")
async def euros_get_prediction(_: RequireEuroWizard) -> dict[str, Any]:
    return noop()


@router.post("/euros/addprediction")
async def euros_add_prediction(_: RequireEuroWizard) -> dict[str, Any]:
    return noop()
