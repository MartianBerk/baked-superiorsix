"""Admin game-ops routes (legacy subdomain: admin). Roles: ADMIN."""

from typing import Any

from fastapi import APIRouter

from baked_superiorsix.api.deps import RequireAdmin
from baked_superiorsix.noop import noop

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/listleagues")
async def list_leagues(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listplayers")
async def list_players(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listrounds")
async def list_rounds(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/currentround")
async def current_round(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/historicrounds")
async def historic_rounds(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listpredictions")
async def list_predictions(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listpredictionsnew")
async def list_predictions_new(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addprediction")
async def add_prediction(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addplayer")
async def add_player(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addround")
async def add_round(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/endround")
async def end_round(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/getround")
async def get_round(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listmatches")
async def list_matches(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listmatchesnew")
async def list_matches_new(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/getmatch")
async def get_match(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addmatch")
async def add_match(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addmatches")
async def add_matches(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/dropmatch")
async def drop_match(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addpredictions")
async def add_predictions(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/dropprediction")
async def drop_prediction(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/setspecialmessage")
async def set_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/getspecialmessage")
async def get_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/endspecialmessage")
async def end_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/dropplayer")
async def drop_player(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/reactivateplayer")
async def reactivate_player(_: RequireAdmin) -> dict[str, Any]:
    return noop()
