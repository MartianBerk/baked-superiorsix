"""Admin game-ops routes (legacy subdomain: admin). Roles: ADMIN."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import (
    RequireAdmin,
    get_league_service,
    get_match_service,
    get_round_service,
)
from baked.superiorsix.api.schemas import (
    CurrentRoundResponse,
    LeagueResponse,
    LeaguesResponse,
    MatchesResponse,
    MatchResponse,
    OpenRoundRequest,
    RoundResponse,
    SelectMatchRequest,
)
from baked.superiorsix.domain.exceptions import (
    MatchNotFoundError,
    MatchSelectionError,
    RoundError,
    RoundNotFoundError,
)
from baked.superiorsix.noop import noop
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.rounds import RoundService

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
async def list_rounds(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> dict[str, list[dict[str, Any]]]:
    rounds = await round_service.list()
    return {
        "rounds": [RoundResponse.from_domain(round_).model_dump(mode="json") for round_ in rounds]
    }


@router.get("/currentround", response_model=CurrentRoundResponse)
async def current_round(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> CurrentRoundResponse:
    round_ = await round_service.current_or_none()
    return CurrentRoundResponse(
        current_round=None if round_ is None else RoundResponse.from_domain(round_)
    )


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


@router.post("/addround", response_model=RoundResponse)
async def add_round(
    _: RequireAdmin,
    payload: OpenRoundRequest,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> RoundResponse:
    try:
        round_ = await round_service.open_round(
            start_date=payload.start_date,
            buy_in_pence=payload.buy_in_pence,
        )
    except RoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoundResponse.from_domain(round_)


@router.post("/endround", response_model=RoundResponse)
async def end_round(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
    end_date: Annotated[str | None, Query(alias="endDate")] = None,
) -> RoundResponse:
    if not end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing endDate",
        )
    day = parse_legacy_date(end_date, field_name="endDate")
    try:
        round_ = await round_service.end_current(day)
    except RoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RoundResponse.from_domain(round_)


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
