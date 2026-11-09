"""Public game routes (legacy subdomain: game)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import (
    get_gameday_service,
    get_match_service,
    get_subround_service,
    get_team_xref_service,
)
from baked.superiorsix.api.schemas import (
    AvailableMatchesResponse,
    AvailableMatchResponse,
    GameMetaEnvelope,
    GameMetaResponse,
    LiveScoreMatchResponse,
    LiveScorePlayerResponse,
    LiveScoresResponse,
    MatchDetailPayload,
    MatchDetailResponse,
    MatchResponse,
    PublicCurrentRoundEnvelope,
    PublicCurrentRoundPayload,
    RoundResponse,
    SubroundResponse,
    SubroundsResponse,
)
from baked.superiorsix.api.team_names import DisplayTeam, load_display
from baked.superiorsix.domain.exceptions import MatchNotFoundError, SubroundNotFoundError
from baked.superiorsix.noop import noop
from baked.superiorsix.services.gameday import GamedayService, LiveScoresBoard
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.subrounds import SubroundService
from baked.superiorsix.services.team_xref import TeamXrefService

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/meta", response_model=GameMetaEnvelope)
async def game_meta(
    gameday: Annotated[GamedayService, Depends(get_gameday_service)],
    team_xref: Annotated[TeamXrefService, Depends(get_team_xref_service)],
) -> GameMetaEnvelope:
    """Header round number + prize pot, plus slate / match-day navigation."""
    meta = await gameday.meta()
    latest = meta.latest_subround
    return GameMetaEnvelope(
        meta=GameMetaResponse(
            round_number=meta.round_number,
            prize_pot_pence=meta.prize_pot_pence,
            buy_in_pence=meta.buy_in_pence,
            n_players=meta.n_players,
            round=None if meta.round_ is None else RoundResponse.from_domain(meta.round_),
            subround=None if latest is None else SubroundResponse.from_status(latest),
            subrounds=[SubroundResponse.from_status(item) for item in meta.subrounds],
            match_days=list(meta.match_days),
            teams=await team_xref.as_map(),
        )
    )


@router.get("/livematches", response_model=AvailableMatchesResponse)
async def game_live_matches(
    match_service: Annotated[MatchService, Depends(get_match_service)],
    team_xref: Annotated[TeamXrefService, Depends(get_team_xref_service)],
    match_date: Annotated[str | None, Query(alias="matchDate")] = None,
    subround_id: Annotated[int | None, Query(alias="subroundId")] = None,
) -> AvailableMatchesResponse:
    """Super Six selections for one slate or legacy UTC calendar day.

    Prefer ``subroundId`` (selected fixtures for that subround). ``matchDate``
    (``dd-mm-yyyy``) remains for day-scoped discovery. Include frozen
    prediction ``difficulty`` when persisted on the match row.
    """
    if subround_id is not None:
        matches = await match_service.selected_matches_for_subround(subround_id)
    elif match_date:
        day = parse_legacy_date(match_date, field_name="matchDate")
        matches = await match_service.available_matches(day)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing matchDate or subroundId",
        )
    display = await load_display(team_xref)
    return AvailableMatchesResponse(
        matches=[
            AvailableMatchResponse.from_domain(match, display_team=display) for match in matches
        ]
    )


@router.get("/livescores", response_model=LiveScoresResponse)
async def game_live_scores(
    gameday: Annotated[GamedayService, Depends(get_gameday_service)],
    team_xref: Annotated[TeamXrefService, Depends(get_team_xref_service)],
    match_date: Annotated[str | None, Query(alias="matchDate")] = None,
    subround_id: Annotated[int | None, Query(alias="subroundId")] = None,
) -> LiveScoresResponse:
    """Aggregate Super Six scoreboard for one slate (or legacy match day).

    Default = latest subround on the open parent round. FE should poll this
    endpoint in batch (~2 min) while fixtures are live — not per match.
    """
    day = None if not match_date else parse_legacy_date(match_date, field_name="matchDate")
    try:
        board = await gameday.live_scores(match_date=day, subround_id=subround_id)
    except SubroundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    display = await load_display(team_xref)
    return _live_scores_response(board, display_team=display)


@router.get("/currentround", response_model=PublicCurrentRoundEnvelope)
async def game_current_round(
    gameday: Annotated[GamedayService, Depends(get_gameday_service)],
) -> PublicCurrentRoundEnvelope:
    """Public pot / round summary. Empty object when no round is open."""
    summary = await gameday.current_round_summary()
    if summary is None:
        return PublicCurrentRoundEnvelope(current_round=None)
    return PublicCurrentRoundEnvelope(
        current_round=PublicCurrentRoundPayload(
            round_id=summary.round_id,
            start_date=summary.start_date,
            round_number=summary.round_number,
            buy_in_pence=summary.buy_in_pence,
            players=summary.players,
            jackpot=summary.jackpot,
            prize_pot_pence=summary.prize_pot_pence,
            subround_id=summary.subround_id,
            current_match_date=summary.current_match_date,
            matches=summary.matches,
        )
    )


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
    return SubroundsResponse(subrounds=[SubroundResponse.from_status(item) for item in statuses])


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


def _live_scores_response(
    board: LiveScoresBoard,
    *,
    display_team: DisplayTeam | None = None,
) -> LiveScoresResponse:
    show = display_team or (lambda name: name)
    return LiveScoresResponse(
        scores=[
            LiveScorePlayerResponse(
                auth_id=player.auth_id,
                id=player.id,
                name=player.name,
                first_name=player.first_name,
                last_name=player.last_name,
                nickname=player.nickname,
                score=player.score,
                matches=[
                    LiveScoreMatchResponse(
                        match_id=pick.match_id,
                        game_number=pick.game_number,
                        home_team=show(pick.home_team),
                        away_team=show(pick.away_team),
                        home_score=pick.home_score,
                        away_score=pick.away_score,
                        status=pick.status,
                        match_date=pick.match_date,
                        match_minute=pick.match_minute,
                        prediction=pick.prediction,
                        correct=pick.correct,
                    )
                    for pick in player.matches
                ],
            )
            for player in board.scores
        ],
        subround=(None if board.subround is None else SubroundResponse.from_status(board.subround)),
        matches=[
            MatchResponse.from_domain(match, display_team=display_team) for match in board.matches
        ],
        match_count=board.match_count,
        live=board.live,
        min_kickoff=board.min_kickoff,
        max_kickoff=board.max_kickoff,
        next_kickoff=board.next_kickoff,
    )
