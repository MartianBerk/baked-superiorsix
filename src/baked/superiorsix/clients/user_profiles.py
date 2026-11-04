"""HTTP client for Superior Six roster profiles in baked-user.

Identity and roles stay in baked-auth JWTs; this client only resolves display /
roster documents keyed by auth ``sub``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from baked.superiorsix.domain.exceptions import RosterError, RosterNotFoundError


@dataclass(slots=True, frozen=True)
class RosterProfile:
    id: UUID
    auth_id: UUID
    first_name: str
    last_name: str
    nickname: str | None
    avatar_url: str | None
    active: bool
    profile: dict[str, Any]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> RosterProfile:
        data = payload.get("profile") or {}
        return cls(
            id=UUID(str(payload["id"])),
            auth_id=UUID(str(payload["auth_id"])),
            first_name=str(data.get("first_name") or ""),
            last_name=str(data.get("last_name") or ""),
            nickname=data.get("nickname"),
            avatar_url=data.get("avatar_url"),
            active=bool(data.get("active", True)),
            profile=dict(data),
        )

    def to_player_dict(self) -> dict[str, Any]:
        """Shape close to legacy PLAYERS rows, keyed by auth_id instead of numeric id."""
        return {
            "auth_id": str(self.auth_id),
            "id": str(self.id),
            "first_name": self.first_name,
            "last_name": self.last_name,
            "nickname": self.nickname,
            "avatar_url": self.avatar_url,
            "retired": not self.active,
            "active": self.active,
        }


class UserProfileGateway(Protocol):
    async def list_profiles(
        self,
        bearer_token: str,
        *,
        active: bool | None = None,
    ) -> list[RosterProfile]: ...

    async def get_by_auth_id(self, bearer_token: str, auth_id: UUID) -> RosterProfile: ...

    async def create_for_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        profile: dict[str, Any],
    ) -> RosterProfile: ...

    async def patch_by_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        patch: dict[str, Any],
    ) -> RosterProfile: ...

    async def patch_me(self, bearer_token: str, patch: dict[str, Any]) -> RosterProfile: ...


class UserProfileClient:
    def __init__(
        self,
        base_url: str,
        *,
        app_name: str = "superiorsix",
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._app_name = app_name
        self._timeout = timeout
        self._client = client

    def _headers(self, bearer_token: str) -> dict[str, str]:
        token = bearer_token.removeprefix("Bearer ").strip()
        return {"Authorization": f"Bearer {token}"}

    async def list_profiles(
        self,
        bearer_token: str,
        *,
        active: bool | None = None,
    ) -> list[RosterProfile]:
        params: dict[str, str] = {}
        if active is not None:
            params["active"] = "true" if active else "false"
        payload = await self._request(
            "GET",
            f"/{self._app_name}/users",
            bearer_token,
            params=params,
        )
        assert isinstance(payload, dict)
        return [RosterProfile.from_api(item) for item in payload.get("profiles", [])]

    async def get_by_auth_id(self, bearer_token: str, auth_id: UUID) -> RosterProfile:
        payload = await self._request(
            "GET",
            f"/{self._app_name}/users/by-auth/{auth_id}",
            bearer_token,
        )
        assert isinstance(payload, dict)
        return RosterProfile.from_api(payload)

    async def create_for_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        profile: dict[str, Any],
    ) -> RosterProfile:
        payload = await self._request(
            "POST",
            f"/{self._app_name}/users/by-auth/{auth_id}",
            bearer_token,
            json={"profile": profile},
        )
        assert isinstance(payload, dict)
        return RosterProfile.from_api(payload)

    async def patch_by_auth_id(
        self,
        bearer_token: str,
        auth_id: UUID,
        patch: dict[str, Any],
    ) -> RosterProfile:
        payload = await self._request(
            "PATCH",
            f"/{self._app_name}/users/by-auth/{auth_id}",
            bearer_token,
            json=patch,
        )
        assert isinstance(payload, dict)
        return RosterProfile.from_api(payload)

    async def patch_me(self, bearer_token: str, patch: dict[str, Any]) -> RosterProfile:
        payload = await self._request(
            "PATCH",
            f"/{self._app_name}/users/me",
            bearer_token,
            json=patch,
        )
        assert isinstance(payload, dict)
        return RosterProfile.from_api(payload)

    async def _request(
        self,
        method: str,
        path: str,
        bearer_token: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._headers(bearer_token)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise RosterError(f"baked-user request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 404:
            raise RosterNotFoundError("Player profile not found.")
        if response.status_code == 409:
            raise RosterError("A profile already exists for this auth id.")
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise RosterError(detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"baked-user returned HTTP {response.status_code}."
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    return f"baked-user returned HTTP {response.status_code}."
