from collections.abc import AsyncIterator
from typing import Annotated

from baked.client.auth import AuthPrincipal
from baked.client.auth.fastapi import get_current_principal, get_key_set, require_roles
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.domain.exceptions import RosterNotFoundError
from baked.superiorsix.repositories.euros import (
    SqlAlchemyEurosMatchRepository,
    SqlAlchemyEurosPredictionRepository,
    SqlAlchemyEurosTournamentRepository,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.predictions import SqlAlchemyPredictionRepository
from baked.superiorsix.repositories.rounds import SqlAlchemyRoundRepository
from baked.superiorsix.repositories.subrounds import SqlAlchemySubroundRepository
from baked.superiorsix.repositories.team_xref import SqlAlchemyTeamXrefRepository
from baked.superiorsix.repositories.worldcup_matches import SqlAlchemyWorldcupMatchRepository
from baked.superiorsix.repositories.worldcup_predictions import (
    SqlAlchemyWorldcupPredictionRepository,
)
from baked.superiorsix.repositories.worldcup_settings import SqlAlchemyWorldcupSettingsRepository
from baked.superiorsix.services.autopick import AutopickService
from baked.superiorsix.services.euros import EurosService
from baked.superiorsix.services.gameday import GamedayService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.stats import StatsService
from baked.superiorsix.services.subrounds import SubroundService
from baked.superiorsix.services.team_xref import TeamXrefService
from baked.superiorsix.services.worldcup import WorldcupService

__all__ = [
    "RequireActivePlayer",
    "RequireAdmin",
    "RequireEuros",
    "RequirePlayer",
    "RequireUserProfile",
    "RequireWorldcup",
    "get_auth_admin",
    "get_autopick_service",
    "get_bearer_token",
    "get_current_principal",
    "get_db_session",
    "get_euros_service",
    "get_gameday_service",
    "get_key_set",
    "get_league_service",
    "get_match_service",
    "get_prediction_service",
    "get_round_service",
    "get_session_factory",
    "get_stats_service",
    "get_subround_service",
    "get_team_xref_service",
    "get_user_profiles",
    "get_worldcup_service",
    "require_active_player",
    "require_roles",
]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def get_db_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def get_team_xref_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TeamXrefService:
    return TeamXrefService(SqlAlchemyTeamXrefRepository(session))


async def get_league_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    team_xref: Annotated[TeamXrefService, Depends(get_team_xref_service)],
) -> LeagueService:
    return LeagueService(
        SqlAlchemyLeagueRepository(session),
        SqlAlchemyMatchRepository(session),
        team_xref,
    )


async def get_match_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    leagues: Annotated[LeagueService, Depends(get_league_service)],
    team_xref: Annotated[TeamXrefService, Depends(get_team_xref_service)],
) -> MatchService:
    return MatchService(SqlAlchemyMatchRepository(session), leagues, team_xref)


def get_user_profiles(request: Request) -> UserProfileGateway:
    return request.app.state.user_profiles


def get_auth_admin(request: Request) -> AuthAdminGateway:
    return request.app.state.auth_admin


async def get_round_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoundService:
    return RoundService(
        SqlAlchemyRoundRepository(session),
        profiles=profiles,
        auth_admin=auth_admin,
        settings=settings,
    )


async def get_subround_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    rounds: Annotated[RoundService, Depends(get_round_service)],
) -> SubroundService:
    return SubroundService(
        SqlAlchemySubroundRepository(session),
        SqlAlchemyMatchRepository(session),
        rounds,
    )


async def get_prediction_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    rounds: Annotated[RoundService, Depends(get_round_service)],
    matches: Annotated[MatchService, Depends(get_match_service)],
) -> PredictionService:
    return PredictionService(SqlAlchemyPredictionRepository(session), rounds, matches)


async def get_autopick_service(
    rounds: Annotated[RoundService, Depends(get_round_service)],
    subrounds: Annotated[SubroundService, Depends(get_subround_service)],
    matches: Annotated[MatchService, Depends(get_match_service)],
    predictions: Annotated[PredictionService, Depends(get_prediction_service)],
) -> AutopickService:
    return AutopickService(
        rounds=rounds,
        subrounds=subrounds,
        matches=matches,
        predictions=predictions,
    )


async def get_gameday_service(
    rounds: Annotated[RoundService, Depends(get_round_service)],
    subrounds: Annotated[SubroundService, Depends(get_subround_service)],
    matches: Annotated[MatchService, Depends(get_match_service)],
    predictions: Annotated[PredictionService, Depends(get_prediction_service)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GamedayService:
    return GamedayService(
        rounds=rounds,
        subrounds=subrounds,
        matches=matches,
        predictions=predictions,
        profiles=profiles,
        auth_admin=auth_admin,
        settings=settings,
    )


async def get_stats_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StatsService:
    return StatsService(
        session,
        profiles=profiles,
        auth_admin=auth_admin,
        settings=settings,
    )


async def get_euros_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
) -> EurosService:
    return EurosService(
        SqlAlchemyEurosTournamentRepository(session),
        SqlAlchemyEurosMatchRepository(session),
        SqlAlchemyEurosPredictionRepository(session),
        profiles=profiles,
        auth_admin=auth_admin,
    )


async def get_worldcup_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
) -> WorldcupService:
    return WorldcupService(
        SqlAlchemyWorldcupMatchRepository(session),
        SqlAlchemyWorldcupPredictionRepository(session),
        SqlAlchemyWorldcupSettingsRepository(session),
        profiles=profiles,
        auth_admin=auth_admin,
    )


def get_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return header.split(" ", 1)[1].strip()


# App-scoped Superior Six roles (JWT aud=superiorsix). Lowercase; hyphen for multi-word.
RequirePlayer = Annotated[AuthPrincipal, Depends(require_roles("player"))]
RequireAdmin = Annotated[AuthPrincipal, Depends(require_roles("admin"))]
RequireUserProfile = Annotated[AuthPrincipal, Depends(require_roles("user-profile"))]
RequireEuros = Annotated[AuthPrincipal, Depends(require_roles("euros"))]
RequireWorldcup = Annotated[AuthPrincipal, Depends(require_roles("worldcup"))]


async def require_active_player(
    principal: RequirePlayer,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
) -> AuthPrincipal:
    """JWT ``player`` role plus an active baked-user roster profile (invite accepted)."""
    try:
        profile = await profiles.get_by_auth_id(token, principal.auth_id)
    except RosterNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not on the Superior Six roster; an invite is required.",
        ) from exc
    if not profile.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Player is inactive on the Superior Six roster.",
        )
    return principal


RequireActivePlayer = Annotated[AuthPrincipal, Depends(require_active_player)]
