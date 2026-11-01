from collections.abc import AsyncIterator
from typing import Annotated

from baked.client.auth import AuthPrincipal
from baked.client.auth.fastapi import get_current_principal, get_key_set, require_roles
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.predictions import SqlAlchemyPredictionRepository
from baked.superiorsix.repositories.rounds import SqlAlchemyRoundRepository
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService

__all__ = [
    "RequireAdmin",
    "RequireDetails",
    "RequireEuroWizard",
    "RequirePlayer",
    "RequireQatarHero",
    "get_current_principal",
    "get_db_session",
    "get_key_set",
    "get_league_service",
    "get_match_service",
    "get_prediction_service",
    "get_round_service",
    "get_session_factory",
    "require_roles",
]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def get_db_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def get_league_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LeagueService:
    return LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))


async def get_match_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    leagues: Annotated[LeagueService, Depends(get_league_service)],
) -> MatchService:
    return MatchService(SqlAlchemyMatchRepository(session), leagues)


async def get_round_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RoundService:
    return RoundService(SqlAlchemyRoundRepository(session))


async def get_prediction_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    rounds: Annotated[RoundService, Depends(get_round_service)],
    matches: Annotated[MatchService, Depends(get_match_service)],
) -> PredictionService:
    return PredictionService(SqlAlchemyPredictionRepository(session), rounds, matches)


# Convenience aliases matching legacy supersix permission names → auth roles.
RequirePlayer = Annotated[AuthPrincipal, Depends(require_roles("PLAYER"))]
RequireAdmin = Annotated[AuthPrincipal, Depends(require_roles("ADMIN"))]
RequireDetails = Annotated[AuthPrincipal, Depends(require_roles("DETAILS"))]
RequireEuroWizard = Annotated[AuthPrincipal, Depends(require_roles("EUROWIZARD"))]
RequireQatarHero = Annotated[AuthPrincipal, Depends(require_roles("QATARHERO"))]
