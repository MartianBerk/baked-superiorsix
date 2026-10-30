"""FastAPI dependencies.

Role checks will validate JWT claims from baked-auth. At this stage they are
declared on routes but do not enforce anything.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends


def require_roles(*roles: str) -> Callable[..., None]:
    """Declare required baked-auth roles for a route (no-op enforcement for now)."""

    async def _dependency() -> None:
        return None

    _dependency.__name__ = f"require_roles_{'_'.join(roles) or 'any'}"
    _dependency.__doc__ = f"Requires baked-auth roles: {', '.join(roles) or '(none)'}"
    return _dependency


# Convenience aliases matching legacy supersix permission names → auth roles.
RequirePlayer = Annotated[None, Depends(require_roles("PLAYER"))]
RequireAdmin = Annotated[None, Depends(require_roles("ADMIN"))]
RequireDetails = Annotated[None, Depends(require_roles("DETAILS"))]
RequireEuroWizard = Annotated[None, Depends(require_roles("EUROWIZARD"))]
RequireQatarHero = Annotated[None, Depends(require_roles("QATARHERO"))]
