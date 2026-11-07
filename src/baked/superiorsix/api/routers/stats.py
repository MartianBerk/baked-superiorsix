"""Public stats routes (legacy subdomain: stats)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import get_stats_service
from baked.superiorsix.api.schemas import (
    AggregatePlayerStats,
    AggregateStatsResponse,
    LookbackComparisonResponse,
)
from baked.superiorsix.services.stats import StatsService

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/aggregate", response_model=AggregateStatsResponse)
async def aggregate_stats(
    stats: Annotated[StatsService, Depends(get_stats_service)],
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
) -> AggregateStatsResponse:
    """Computed success rates over the one-round lookback window.

    Default window = current open round + previous closed round. Optional
    ``start_date`` / ``end_date`` (``dd-mm-yyyy``) further filter match days.
    ``playerid`` is roster ``auth_id``. FE renders ``success_rate`` as-is.
    """
    start = None if not start_date else parse_legacy_date(start_date, field_name="start_date")
    end = None if not end_date else parse_legacy_date(end_date, field_name="end_date")
    rows = await stats.aggregate(start_date=start, end_date=end)
    return AggregateStatsResponse(stats=[AggregatePlayerStats.from_domain(row) for row in rows])


@router.get("/winners", response_model=LookbackComparisonResponse)
async def winners(
    stats: Annotated[StatsService, Depends(get_stats_service)],
) -> LookbackComparisonResponse:
    """Current vs previous round comparison (not a full winners archive).

    ``previous_round`` includes winners of the just-ended / last closed round.
    """
    return LookbackComparisonResponse.from_domain(await stats.lookback())
