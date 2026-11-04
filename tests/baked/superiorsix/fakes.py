from typing import Any
from uuid import UUID, uuid4

from baked.superiorsix.clients.user_profiles import RosterProfile
from baked.superiorsix.domain.exceptions import RosterError, RosterNotFoundError


class FakeUserProfileClient:
    """In-memory baked-user stand-in for roster route tests."""

    def __init__(self) -> None:
        self._by_auth: dict[UUID, RosterProfile] = {}

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
            avatar_url=extra.get("avatar_url"),
            active=True,
            profile=profile,
        )
        self._by_auth[auth_id] = created
        return created

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
    ) -> RosterProfile:
        del bearer_token
        if auth_id in self._by_auth:
            raise RosterError("A profile already exists for this auth id.")
        created = RosterProfile(
            id=uuid4(),
            auth_id=auth_id,
            first_name=str(profile["first_name"]),
            last_name=str(profile["last_name"]),
            nickname=profile.get("nickname"),
            avatar_url=profile.get("avatar_url"),
            active=bool(profile.get("active", True)),
            profile={**profile, "active": bool(profile.get("active", True))},
        )
        self._by_auth[auth_id] = created
        return created

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
