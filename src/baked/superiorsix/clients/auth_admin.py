"""Server-side baked-auth admin client for invite-ready users + memberships.

Uses deploy/bootstrap credentials (``aud=auth``) for
``POST /v1/users`` and ``PUT /v1/applications/{slug}/memberships/{email}``.
Invitee password login uses the public token endpoint (no admin creds).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID

import httpx

from baked.superiorsix.domain.exceptions import AuthAdminError


@dataclass(slots=True, frozen=True)
class MembershipResult:
    application: str
    roles: list[str]


@dataclass(slots=True, frozen=True)
class InviteReadyUserResult:
    id: UUID
    email: str
    must_set_password: bool
    memberships: list[MembershipResult]


@dataclass(slots=True, frozen=True)
class AuthUserAdminResult:
    id: UUID
    email: str
    is_active: bool
    must_set_password: bool


class AuthAdminGateway(Protocol):
    async def create_invite_ready_user(
        self,
        *,
        email: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> InviteReadyUserResult: ...

    async def get_user(self, *, user_id: UUID) -> AuthUserAdminResult: ...

    async def patch_user(self, *, user_id: UUID, is_active: bool) -> AuthUserAdminResult: ...

    async def reset_user_password(self, *, user_id: UUID) -> AuthUserAdminResult: ...

    async def get_membership(
        self,
        *,
        email: str,
        application: str = "superiorsix",
    ) -> MembershipResult: ...

    async def set_membership(
        self,
        *,
        email: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> MembershipResult: ...

    async def login_user(self, *, email: str, password: str, audience: str) -> str: ...


class AuthAdminClient:
    def __init__(
        self,
        *,
        token_url: str,
        email: str,
        password: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_url = token_url
        self._email = email
        self._password = password
        self._timeout = timeout
        self._client = client
        self._auth_base = token_url.rsplit("/v1/auth/token", 1)[0]

    async def create_invite_ready_user(
        self,
        *,
        email: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> InviteReadyUserResult:
        """POST /v1/users — passwordless user + optional membership (SS-66)."""
        token = await self._login_auth_admin()
        body: dict[str, Any] = {
            "email": email.strip().lower(),
            "membership": {
                "application": application,
                "roles": list(roles),
            },
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                f"{self._auth_base}/v1/users",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth invite-ready create failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 409:
            raise AuthAdminError("EMAIL_EXISTS:" + _error_detail(response))
        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        return _invite_ready_from_payload(response.json(), application=application)

    async def get_user(self, *, user_id: UUID) -> AuthUserAdminResult:
        token = await self._login_auth_admin()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(
                f"{self._auth_base}/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth user read failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        return _auth_user_from_payload(response.json())

    async def patch_user(self, *, user_id: UUID, is_active: bool) -> AuthUserAdminResult:
        token = await self._login_auth_admin()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.patch(
                f"{self._auth_base}/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"is_active": is_active},
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth user patch failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        return _auth_user_from_payload(response.json())

    async def reset_user_password(self, *, user_id: UUID) -> AuthUserAdminResult:
        token = await self._login_auth_admin()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                f"{self._auth_base}/v1/users/{user_id}/reset-password",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"baked-auth reset-password failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise AuthAdminError(_error_detail(response))
        return _auth_user_from_payload(response.json())

    async def get_membership(
        self,
        *,
        email: str,
        application: str = "superiorsix",
    ) -> MembershipResult:
        token = await self._login_auth_admin()
        encoded = quote(email.strip().lower(), safe="")
        url = f"{self._auth_base}/v1/applications/{application}/memberships/{encoded}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
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

    async def set_membership(
        self,
        *,
        email: str,
        roles: list[str],
        application: str = "superiorsix",
    ) -> MembershipResult:
        token = await self._login_auth_admin()
        encoded = quote(email.strip().lower(), safe="")
        url = f"{self._auth_base}/v1/applications/{application}/memberships/{encoded}"
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

    async def login_user(self, *, email: str, password: str, audience: str) -> str:
        """Password login; returns access_token for the given audience."""
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                self._token_url,
                data={
                    "email": email.strip().lower(),
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
        if not self._email or not self._password:
            raise AuthAdminError(
                "Auth admin credentials are not configured "
                "(SUPERIORSIX_AUTH_ADMIN_EMAIL / SUPERIORSIX_AUTH_ADMIN_PASSWORD)."
            )
        return await self.login_user(
            email=self._email,
            password=self._password,
            audience="auth",
        )


def _auth_user_from_payload(payload: dict[str, Any]) -> AuthUserAdminResult:
    raw_id = payload.get("id")
    if raw_id is None:
        raise AuthAdminError("baked-auth user response missing id.")
    return AuthUserAdminResult(
        id=UUID(str(raw_id)),
        email=str(payload.get("email") or ""),
        is_active=bool(payload.get("is_active", True)),
        must_set_password=bool(payload.get("must_set_password", False)),
    )


def _invite_ready_from_payload(
    payload: dict[str, Any],
    *,
    application: str,
) -> InviteReadyUserResult:
    raw_id = payload.get("id")
    if raw_id is None:
        raise AuthAdminError("baked-auth invite-ready response missing id.")
    memberships: list[MembershipResult] = []
    for item in payload.get("memberships") or []:
        if not isinstance(item, dict):
            continue
        memberships.append(
            MembershipResult(
                application=str(item.get("application") or application),
                roles=[str(role) for role in (item.get("roles") or [])],
            )
        )
    return InviteReadyUserResult(
        id=UUID(str(raw_id)),
        email=str(payload.get("email") or ""),
        must_set_password=bool(payload.get("must_set_password", True)),
        memberships=memberships,
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
