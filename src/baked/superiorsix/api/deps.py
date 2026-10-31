from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.core.security import JwtKeySet, principal_from_token
from baked.superiorsix.domain.exceptions import AuthenticationError
from baked.superiorsix.domain.models import AuthPrincipal


def get_oauth2_scheme(settings: Settings) -> OAuth2PasswordBearer:
    return OAuth2PasswordBearer(tokenUrl=settings.auth_token_url)


async def get_bearer_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    return await get_oauth2_scheme(settings)(request)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def get_key_set(request: Request) -> JwtKeySet:
    return request.app.state.key_set


async def get_db_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def get_current_principal(
    token: Annotated[str, Depends(get_bearer_token)],
    key_set: Annotated[JwtKeySet, Depends(get_key_set)],
) -> AuthPrincipal:
    try:
        return principal_from_token(token, key_set)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_roles(*roles: str) -> Callable[..., AuthPrincipal]:
    """Require any of the given baked-auth roles on the current principal."""

    async def dependency(
        principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    ) -> AuthPrincipal:
        if not set(roles).intersection(principal.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return principal

    dependency.__name__ = f"require_roles_{'_'.join(roles) or 'any'}"
    return dependency


# Convenience aliases matching legacy supersix permission names → auth roles.
RequirePlayer = Annotated[AuthPrincipal, Depends(require_roles("PLAYER"))]
RequireAdmin = Annotated[AuthPrincipal, Depends(require_roles("ADMIN"))]
RequireDetails = Annotated[AuthPrincipal, Depends(require_roles("DETAILS"))]
RequireEuroWizard = Annotated[AuthPrincipal, Depends(require_roles("EUROWIZARD"))]
RequireQatarHero = Annotated[AuthPrincipal, Depends(require_roles("QATARHERO"))]
