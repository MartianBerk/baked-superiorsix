"""Public stats routes (legacy subdomain: stats)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import get_stats_service
from baked.superiorsix.api.schemas import (
    AggregatePlayerStats,
    AggregateScorePoint,
    AggregateStatsResponse,
    HistoricRoundResponse,
    HistoricRoundsResponse,
)
from baked.superiorsix.services.stats import HistoricRound, PlayerAggregate, StatsService

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/aggregate", response_model=AggregateStatsResponse)
async def aggregate_stats(
    stats: Annotated[StatsService, Depends(get_stats_service)],
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> AggregateStatsResponse:
    """Player correct-pick aggregates by match day (legacy ``PLAYER_STATS_AGG``).

    Optional ``start_date`` / ``end_date`` use legacy ``dd-mm-yyyy``.
    ``playerid`` is the roster ``auth_id`` UUID (replaces legacy numeric player id).
    """
    start = None if not start_date else parse_legacy_date(start_date, field_name="start_date")
    end = None if not end_date else parse_legacy_date(end_date, field_name="end_date")
    rows = await stats.aggregate(start_date=start, end_date=end)
    return AggregateStatsResponse(stats=[_aggregate_player(row) for row in rows])


@router.get("/winners", response_model=HistoricRoundsResponse)
async def winners(
    stats: Annotated[StatsService, Depends(get_stats_service)],
) -> HistoricRoundsResponse:
    """Historic closed rounds with winners and jackpots (newest start first)."""
    rounds = await stats.historic_rounds()
    return HistoricRoundsResponse(rounds=[_historic_round(row) for row in rounds])


def _aggregate_player(row: PlayerAggregate) -> AggregatePlayerStats:
    return AggregatePlayerStats(
        playerid=row.auth_id,
        name=row.name,
        scores=[
            AggregateScorePoint(
                name=score.name,
                date=score.match_date,
                score=score.correct,
                matches=score.matches,
            )
            for score in row.scores
        ],
    )


def _historic_round(row: HistoricRound) -> HistoricRoundResponse:
    return HistoricRoundResponse(
        round_id=row.round_id,
        start_date=row.start_date,
        end_date=row.end_date,
        matches=row.matches,
        players=row.players,
        jackpot=row.jackpot,
        winner=row.winner,
        winner_ids=list(row.winner_ids),
    )
