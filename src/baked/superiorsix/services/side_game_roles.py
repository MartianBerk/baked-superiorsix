"""Grant app-scoped side-game roles (``euros`` / ``worldcup``) to selected roster."""

from __future__ import annotations

from uuid import UUID

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.domain.exceptions import AuthAdminError, RosterError, RosterNotFoundError

_ALWAYS_GRANT = ("user-profile",)


def dedupe_entrant_ids(entrant_ids: list[UUID]) -> list[UUID]:
    """Preserve order while dropping duplicate auth ids."""
    seen: set[UUID] = set()
    out: list[UUID] = []
    for auth_id in entrant_ids:
        if auth_id in seen:
            continue
        seen.add(auth_id)
        out.append(auth_id)
    return out


def _with_always_grant(roles: list[str]) -> list[str]:
    seen = {role.strip().lower() for role in roles if role and role.strip()}
    out = [role for role in roles if role and role.strip()]
    for role in _ALWAYS_GRANT:
        if role not in seen:
            out.append(role)
            seen.add(role)
    return out


async def grant_side_game_role(
    *,
    entrant_ids: list[UUID],
    role: str,
    bearer_token: str,
    profiles: UserProfileGateway,
    auth_admin: AuthAdminGateway,
    application: str = "superiorsix",
) -> list[UUID]:
    """Resolve roster profiles and merge *role* into each membership.

    Returns the deduped entrant list that was granted. Raises ``ValueError`` when
    the list is empty or a selected auth_id is missing / has no email.
    Propagates ``AuthAdminError`` / ``RosterError`` from gateways.
    """
    unique = dedupe_entrant_ids(list(entrant_ids))
    if not unique:
        raise ValueError("entrant_ids must not be empty.")

    role_key = role.strip().lower()
    for auth_id in unique:
        try:
            profile = await profiles.get_by_auth_id(bearer_token, auth_id)
        except RosterNotFoundError as exc:
            raise ValueError(f"Entrant {auth_id} is not on the roster.") from exc
        except RosterError:
            raise
        email = (profile.email or "").strip()
        if not email:
            raise ValueError(f"Entrant {auth_id} has no email on their profile.")
        try:
            membership = await auth_admin.get_membership(
                email=email,
                application=application,
            )
            current = list(membership.roles)
        except AuthAdminError:
            # No membership yet — start from always-grant + side-game role.
            current = []
        if role_key not in {r.strip().lower() for r in current if r}:
            current.append(role_key)
        roles = _with_always_grant(current)
        await auth_admin.set_membership(
            email=email,
            roles=roles,
            application=application,
        )
    return unique
