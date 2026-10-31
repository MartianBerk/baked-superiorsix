"""Admin game-ops routes (legacy subdomain: admin). Roles: ADMIN."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import RequireAdmin, get_league_service, get_match_service
from baked.superiorsix.api.schemas import (
    LeagueResponse,
    LeaguesResponse,
    MatchesResponse,
    MatchResponse,
    SelectMatchRequest,
)
from baked.superiorsix.domain.exceptions import MatchNotFoundError, MatchSelectionError
from baked.superiorsix.noop import noop
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/listleagues", response_model=LeaguesResponse)
async def list_leagues(
    _: RequireAdmin,
    league_service: Annotated[LeagueService, Depends(get_league_service)],
) -> LeaguesResponse:
    leagues = await league_service.list()
    return LeaguesResponse(leagues=[LeagueResponse.from_domain(league) for league in leagues])


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


@router.get("/listmatches", response_model=MatchesResponse)
async def list_matches(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    match_date: Annotated[str | None, Query(alias="matchDate")] = None,
) -> MatchesResponse:
    if not match_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing matchDate",
        )
    day = parse_legacy_date(match_date, field_name="matchDate")
    matches = await match_service.list_on_date(day)
    return MatchesResponse(matches=[MatchResponse.from_domain(match) for match in matches])


@router.get("/listmatchesnew")
async def list_matches_new(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/getmatch")
async def get_match(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.post("/addmatch", response_model=MatchResponse)
async def add_match(
    _: RequireAdmin,
    payload: SelectMatchRequest,
    match_service: Annotated[MatchService, Depends(get_match_service)],
) -> MatchResponse:
    try:
        match = await match_service.select_for_round(payload.id, payload.game_number)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MatchSelectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MatchResponse.from_domain(match)


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
