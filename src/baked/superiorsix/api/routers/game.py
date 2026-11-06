"""Public game routes (legacy subdomain: game)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import get_match_service, get_subround_service
from baked.superiorsix.api.schemas import (
    AvailableMatchesResponse,
    AvailableMatchResponse,
    MatchDetailPayload,
    MatchDetailResponse,
    SubroundResponse,
    SubroundsResponse,
)
from baked.superiorsix.domain.exceptions import MatchNotFoundError
from baked.superiorsix.noop import noop
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.subrounds import SubroundService

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/meta")
async def game_meta() -> dict[str, Any]:
    return noop()


@router.get("/livematches", response_model=AvailableMatchesResponse)
async def game_live_matches(
    match_service: Annotated[MatchService, Depends(get_match_service)],
    match_date: Annotated[str | None, Query(alias="matchDate")] = None,
) -> AvailableMatchesResponse:
    if not match_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing matchDate",
        )
    day = parse_legacy_date(match_date, field_name="matchDate")
    matches = await match_service.available_matches(day)
    return AvailableMatchesResponse(
        matches=[AvailableMatchResponse.from_domain(match) for match in matches]
    )


@router.get("/livescores")
async def game_live_scores() -> dict[str, Any]:
    return noop()


@router.get("/currentround")
async def game_current_round() -> dict[str, Any]:
    return noop()


@router.get("/listsubrounds", response_model=SubroundsResponse)
async def game_list_subrounds(
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    round_id: Annotated[int | None, Query(alias="roundId")] = None,
) -> SubroundsResponse:
    """Player-facing subrounds for the current (or given) parent round.

    Does not bootstrap a round — returns ``[]`` when none is open.
    """
    if round_id is None:
        statuses = await subround_service.list_for_current_round()
    else:
        statuses = await subround_service.list_for_round(round_id)
    return SubroundsResponse(
        subrounds=[SubroundResponse.from_status(item) for item in statuses]
    )


@router.get("/specialmessage")
async def game_special_message() -> dict[str, Any]:
    return noop()


@router.get("/matchdetail", response_model=MatchDetailResponse)
async def game_match_detail(
    match_service: Annotated[MatchService, Depends(get_match_service)],
    home_team: Annotated[str | None, Query(alias="hometeam")] = None,
    away_team: Annotated[str | None, Query(alias="awayteam")] = None,
    match_date: Annotated[str | None, Query(alias="matchdate")] = None,
) -> MatchDetailResponse:
    missing = [
        name
        for name, value in (
            ("hometeam", home_team),
            ("awayteam", away_team),
            ("matchdate", match_date),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing mandatory parameter '{missing[0]}'.",
        )
    assert home_team is not None and away_team is not None and match_date is not None
    day = parse_legacy_date(match_date, field_name="matchdate")
    try:
        detail = await match_service.match_detail(home_team, away_team, day)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MatchDetailResponse(match_detail=MatchDetailPayload.from_domain(detail))
