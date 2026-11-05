"""Server-side baked-auth admin client for membership grants.

Uses deploy/bootstrap credentials (aud=auth) — never the invitee's token —
to ``PUT /v1/applications/{slug}/memberships/{username}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from baked.superiorsix.domain.exceptions import AuthAdminError


@dataclass(slots=True, frozen=True)
class MembershipResult:
    application: str
    roles: list[str]


class AuthAdminGateway(Protocol):
    async def set_membership(
        self,
        *,
        username: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> MembershipResult: ...


class AuthAdminClient:
    def __init__(
        self,
        *,
        token_url: str,
        username: str,
        password: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_url = token_url
        self._username = username
        self._password = password
        self._timeout = timeout
        self._client = client
        self._auth_base = token_url.rsplit("/v1/auth/token", 1)[0]

    async def set_membership(
        self,
        *,
        username: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> MembershipResult:
        token = await self._login_auth_admin()
        url = (
            f"{self._auth_base}/v1/applications/{application}/memberships/"
            f"{username.strip().lower()}"
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.put(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"roles": list(roles)},
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth membership request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        payload = response.json()
        return MembershipResult(
            application=str(payload.get("application") or application),
            roles=[str(role) for role in (payload.get("roles") or [])],
        )

    async def login_user(self, *, username: str, password: str, audience: str) -> str:
        """OAuth2 password login; returns access_token for the given audience."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                self._token_url,
                data={
                    "username": username.strip().lower(),
                    "password": password,
                    "audience": audience,
                },
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth token request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthAdminError("baked-auth token response missing access_token.")
        return token

    async def _login_auth_admin(self) -> str:
        if not self._username or not self._password:
            raise AuthAdminError(
                "Auth admin credentials are not configured "
                "(SUPERIORSIX_AUTH_ADMIN_USERNAME / SUPERIORSIX_AUTH_ADMIN_PASSWORD)."
            )
        return await self.login_user(
            username=self._username,
            password=self._password,
            audience="auth",
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"baked-auth returned HTTP {response.status_code}."
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    return f"baked-auth returned HTTP {response.status_code}."
