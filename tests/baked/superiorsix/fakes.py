from typing import Any
from uuid import UUID, uuid4

from baked.superiorsix.clients.auth_admin import InviteReadyUserResult, MembershipResult
from baked.superiorsix.clients.user_profiles import RosterInvite, RosterProfile
from baked.superiorsix.domain.exceptions import AuthAdminError, RosterError, RosterNotFoundError


class FakeUserProfileClient:
    """In-memory baked-user stand-in for roster route tests."""

    def __init__(self) -> None:
        self._by_auth: dict[UUID, RosterProfile] = {}
        self._invites: dict[str, RosterInvite] = {}
        self._auth_id_by_username: dict[str, UUID] = {}

    def seed(
        self,
        auth_id: UUID,
        *,
        first_name: str,
        last_name: str,
        **extra: Any,
    ) -> RosterProfile:
        profile = {
            "first_name": first_name,
            "last_name": last_name,
            "active": True,
            **extra,
        }
        created = RosterProfile(
            id=uuid4(),
            auth_id=auth_id,
            first_name=first_name,
            last_name=last_name,
            nickname=extra.get("nickname"),
            email=extra.get("email"),
            avatar_url=extra.get("avatar_url"),
            active=True,
            profile=profile,
        )
        self._by_auth[auth_id] = created
        return created

    def bind_username(self, username: str, auth_id: UUID) -> None:
        self._auth_id_by_username[username.lower()] = auth_id

    async def list_profiles(
        self,
        bearer_token: str,
        *,
        active: bool | None = None,
    ) -> list[RosterProfile]:
        del bearer_token
        profiles = list(self._by_auth.values())
        if active is None:
            return profiles
        return [profile for profile in profiles if profile.active is active]

    async def get_by_auth_id(self, bearer_token: str, auth_id: UUID) -> RosterProfile:
        del bearer_token
        profile = self._by_auth.get(auth_id)
        if profile is None:
            raise RosterNotFoundError("Player profile not found.")
        return profile

    async def create_for_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        profile: dict[str, Any],
        *,
        username: str | None = None,
    ) -> RosterProfile:
        del bearer_token
        if username is None:
            raise RosterError("A username is required to accept an invite for this application.")
        invite = self._invites.get(username.lower())
        if invite is None or invite.status != "pending":
            raise RosterError(
                f"No pending invite for username '{username}' on 'superiorsix'."
            )
        if auth_id in self._by_auth:
            raise RosterError("A profile already exists for this auth id.")
        merged = {**invite.profile, **profile}
        created = RosterProfile(
            id=uuid4(),
            auth_id=auth_id,
            first_name=str(merged["first_name"]),
            last_name=str(merged["last_name"]),
            nickname=merged.get("nickname"),
            email=merged.get("email"),
            avatar_url=merged.get("avatar_url"),
            active=bool(merged.get("active", True)),
            profile={**merged, "active": bool(merged.get("active", True))},
        )
        self._by_auth[auth_id] = created
        self._invites[username.lower()] = RosterInvite(
            id=invite.id,
            username=invite.username,
            status="accepted",
            auth_id=auth_id,
            profile=dict(invite.profile),
            roles=list(invite.roles),
        )
        return created

    async def create_me(
        self,
        bearer_token: str,
        profile: dict[str, Any],
    ) -> tuple[RosterProfile, list[str] | None]:
        # Tests pass a token shaped like "user:<username>" or use bind_username.
        username = _username_from_token(bearer_token)
        auth_id = self._auth_id_by_username.get(username)
        if auth_id is None:
            auth_id = uuid4()
            self._auth_id_by_username[username] = auth_id
        created = await self.create_for_auth_id(
            bearer_token,
            auth_id,
            profile,
            username=username,
        )
        invite = self._invites[username]
        return created, list(invite.roles)

    async def patch_by_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        patch: dict[str, Any],
    ) -> RosterProfile:
        del bearer_token
        existing = self._by_auth.get(auth_id)
        if existing is None:
            raise RosterNotFoundError("Player profile not found.")
        merged = {**existing.profile, **patch}
        updated = RosterProfile(
            id=existing.id,
            auth_id=existing.auth_id,
            first_name=str(merged.get("first_name") or existing.first_name),
            last_name=str(merged.get("last_name") or existing.last_name),
            nickname=merged.get("nickname"),
            email=merged.get("email"),
            avatar_url=merged.get("avatar_url"),
            active=bool(merged.get("active", True)),
            profile=merged,
        )
        self._by_auth[auth_id] = updated
        return updated

    async def patch_me(self, bearer_token: str, patch: dict[str, Any]) -> RosterProfile:
        # Tests that need patch_me seed a single profile and patch the only entry.
        del bearer_token
        if len(self._by_auth) != 1:
            raise RosterNotFoundError("Player profile not found.")
        auth_id = next(iter(self._by_auth))
        return await self.patch_by_auth_id("unused", auth_id, patch)

    async def invite(
        self,
        bearer_token: str,
        username: str,
        *,
        profile: dict[str, Any] | None = None,
        roles: list[str] | None = None,
    ) -> RosterInvite:
        del bearer_token
        key = username.lower()
        existing = self._invites.get(key)
        if existing is not None and existing.status == "accepted":
            raise RosterError(f"Username '{key}' already accepted an invite for 'superiorsix'.")
        created = RosterInvite(
            id=uuid4(),
            username=key,
            status="pending",
            auth_id=None,
            profile=dict(profile or {}),
            roles=list(roles or []),
        )
        self._invites[key] = created
        return created

    async def list_invites(self, bearer_token: str) -> list[RosterInvite]:
        del bearer_token
        return list(self._invites.values())

    async def get_invite(self, bearer_token: str, username: str) -> RosterInvite:
        del bearer_token
        invite = self._invites.get(username.lower())
        if invite is None:
            raise RosterError(f"No invite for username '{username}' on 'superiorsix'.")
        return invite


class FakeAuthAdminClient:
    """In-memory baked-auth admin stand-in for invite / accept-invite tests."""

    def __init__(self) -> None:
        self.memberships: dict[str, list[str]] = {}
        self.passwords: dict[str, str | None] = {}
        self.tokens_issued: list[tuple[str, str]] = []
        self.fail_login_for: set[str] = set()
        self.created_invite_ready: list[str] = []

    def register(self, username: str, password: str) -> None:
        self.passwords[username.lower()] = password

    def set_password(self, username: str, password: str) -> None:
        key = username.lower()
        if key not in self.passwords:
            raise AuthAdminError("User not found.")
        if self.passwords[key] is not None:
            raise AuthAdminError("Password already set; use login instead of set-password.")
        self.passwords[key] = password

    async def create_invite_ready_user(
        self,
        *,
        username: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> InviteReadyUserResult:
        key = username.strip().lower()
        if key in self.passwords:
            raise AuthAdminError("USERNAME_EXISTS:Username is already registered.")
        self.passwords[key] = None
        self.memberships[key] = list(roles)
        self.created_invite_ready.append(key)
        return InviteReadyUserResult(
            id=uuid4(),
            username=key,
            must_set_password=True,
            memberships=[MembershipResult(application=application, roles=list(roles))],
        )

    async def set_membership(
        self,
        *,
        username: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> MembershipResult:
        key = username.strip().lower()
        self.memberships[key] = list(roles)
        return MembershipResult(application=application, roles=list(roles))

    async def login_user(self, *, username: str, password: str, audience: str) -> str:
        key = username.strip().lower()
        if key in self.fail_login_for:
            raise AuthAdminError("Incorrect username or password.")
        if key not in self.passwords:
            raise AuthAdminError("Incorrect username or password.")
        stored = self.passwords[key]
        if stored is None:
            raise AuthAdminError(
                "Password not set; complete set-password before login."
            )
        if stored != password:
            raise AuthAdminError("Incorrect username or password.")
        if audience == "superiorsix" and key not in self.memberships and key != "ops":
            raise AuthAdminError(f"User is not a member of application '{audience}'.")
        self.tokens_issued.append((key, audience))
        return f"token:{key}:{audience}"


def _username_from_token(bearer_token: str) -> str:
    token = bearer_token.removeprefix("Bearer ").strip()
    if token.startswith("token:"):
        parts = token.split(":")
        if len(parts) >= 2:
            return parts[1]
    if token.startswith("user:"):
        return token.split(":", 1)[1]
    return "unknown"
