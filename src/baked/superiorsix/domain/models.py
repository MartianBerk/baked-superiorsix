from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    """Identity taken from a verified baked-auth access token."""

    auth_id: UUID
    username: str | None = None
    roles: tuple[str, ...] = ()
