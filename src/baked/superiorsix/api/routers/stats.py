"""Public stats routes (legacy subdomain: stats)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import get_stats_service
from baked.superiorsix.api.schemas import (
    AggregatePlayerStats,
    AggregateStatsResponse,
    HistoricRoundResponse,
    HistoricRoundsResponse,
)
from baked.superiorsix.services.stats import StatsService

router = APIRouter(prefix="/stats", tags=["stats"])

AggregateScopeQuery = Literal["current", "previous"]


@router.get("/aggregate", response_model=AggregateStatsResponse)
async def aggregate_stats(
    stats: Annotated[StatsService, Depends(get_stats_service)],
    scope: Annotated[
        AggregateScopeQuery,
        Query(description="current = open round (n); previous = last closed (n−1)"),
    ] = "previous",
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> AggregateStatsResponse:
    """Computed success rates for one round scope.

    Default ``scope=previous`` keeps the SS-18 n−1 contract. Pass
    ``scope=current`` for the open round (Performance graph / table). Optional
    ``start_date`` / ``end_date`` (``dd-mm-yyyy``) further filter match days.
    ``playerid`` is roster ``auth_id``. FE renders ``success_rate`` as-is.
    """
    start = None if not start_date else parse_legacy_date(start_date, field_name="start_date")
    end = None if not end_date else parse_legacy_date(end_date, field_name="end_date")
    rows = await stats.aggregate(scope=scope, start_date=start, end_date=end)
    return AggregateStatsResponse(stats=[AggregatePlayerStats.from_domain(row) for row in rows])


@router.get("/winners", response_model=HistoricRoundsResponse)
async def winners(
    stats: Annotated[StatsService, Depends(get_stats_service)],
) -> HistoricRoundsResponse:
    """Full multi-round historic winners archive (newest start first)."""
    rounds = await stats.historic_rounds()
    return HistoricRoundsResponse(
        rounds=[HistoricRoundResponse.from_domain(row) for row in rounds]
    )
