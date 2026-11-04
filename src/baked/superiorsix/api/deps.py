from collections.abc import AsyncIterator
from typing import Annotated

from baked.client.auth import AuthPrincipal
from baked.client.auth.fastapi import get_current_principal, get_key_set, require_roles
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.domain.exceptions import RosterNotFoundError
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.predictions import SqlAlchemyPredictionRepository
from baked.superiorsix.repositories.rounds import SqlAlchemyRoundRepository
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService

__all__ = [
    "RequireActivePlayer",
    "RequireAdmin",
    "RequireAdminOrGameMaster",
    "RequireEuros",
    "RequireGameMaster",
    "RequirePlayer",
    "RequireUserProfile",
    "RequireWorldcup",
    "get_bearer_token",
    "get_current_principal",
    "get_db_session",
    "get_key_set",
    "get_league_service",
    "get_match_service",
    "get_prediction_service",
    "get_round_service",
    "get_session_factory",
    "get_user_profiles",
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


def get_user_profiles(request: Request) -> UserProfileGateway:
    return request.app.state.user_profiles


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
RequireGameMaster = Annotated[AuthPrincipal, Depends(require_roles("game-master"))]
RequireUserProfile = Annotated[AuthPrincipal, Depends(require_roles("user-profile"))]
RequireEuros = Annotated[AuthPrincipal, Depends(require_roles("euros"))]
RequireWorldcup = Annotated[AuthPrincipal, Depends(require_roles("worldcup"))]
# Roster/list views used by both user-admin and match-day operators.
RequireAdminOrGameMaster = Annotated[
    AuthPrincipal,
    Depends(require_roles("admin", "game-master")),
]


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
