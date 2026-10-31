"""JWT access-token verification for baked-auth tokens.

Mirrors baked-user's verifier so Superior Six can check roles from the same
access tokens without round-tripping baked-auth on every request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWK

from baked.superiorsix.core.config import Settings
from baked.superiorsix.domain.exceptions import AuthenticationError
from baked.superiorsix.domain.models import AuthPrincipal

_ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True, slots=True)
class JwtKey:
    """One verification key. ``material`` is a shared secret or a public key."""

    algorithm: str
    material: Any
    kid: str | None = None


@dataclass(frozen=True, slots=True)
class JwtKeySet:
    """Ordered verification keys. The first matching key is the current signer."""

    keys: tuple[JwtKey, ...]

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("JWT key set must contain at least one key.")
        algorithms = {key.algorithm.upper() for key in self.keys}
        if "NONE" in algorithms:
            raise ValueError("The 'none' JWT algorithm is not allowed.")

    def verification_keys(self, kid: str | None) -> tuple[JwtKey, ...]:
        if kid:
            matched = tuple(key for key in self.keys if key.kid == kid)
            if not matched:
                raise AuthenticationError("Invalid or expired access token.")
            return matched
        return self.keys

    @classmethod
    def from_jwks(cls, document: dict[str, Any]) -> JwtKeySet:
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ValueError("JWKS document must contain a non-empty 'keys' array.")

        keys: list[JwtKey] = []
        for index, entry in enumerate(raw_keys):
            if not isinstance(entry, dict):
                raise ValueError(f"JWKS keys[{index}] must be an object.")
            algorithm = str(entry.get("alg") or "").strip().upper()
            if not algorithm:
                raise ValueError(f"JWKS keys[{index}] is missing 'alg'.")
            if algorithm == "NONE":
                raise ValueError("The 'none' JWT algorithm is not allowed.")
            jwk = PyJWK.from_dict(entry, algorithm=algorithm)
            keys.append(JwtKey(kid=entry.get("kid"), algorithm=algorithm, material=jwk.key))
        return cls(tuple(keys))

    @classmethod
    def from_jwks_file(cls, path: Path) -> JwtKeySet:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("JWKS file must contain a JSON object.")
        return cls.from_jwks(document)

    @classmethod
    def from_shared_secrets(
        cls,
        *,
        current: str,
        previous: list[str] | tuple[str, ...] = (),
        algorithm: str = "HS256",
        current_kid: str | None = None,
    ) -> JwtKeySet:
        keys = [JwtKey(kid=current_kid, algorithm=algorithm, material=current)]
        for index, secret in enumerate(previous):
            keys.append(JwtKey(kid=f"previous-{index}", algorithm=algorithm, material=secret))
        return cls(tuple(keys))


def load_key_set(settings: Settings) -> JwtKeySet:
    """Build the verification key set from JWKS or shared HS* secrets."""
    if settings.jwt_jwks_path is not None:
        return JwtKeySet.from_jwks_file(settings.jwt_jwks_path)
    return JwtKeySet.from_shared_secrets(
        current=settings.jwt_secret_key.get_secret_value(),
        previous=[secret.get_secret_value() for secret in settings.jwt_previous_secret_keys],
        algorithm=settings.jwt_algorithm,
        current_kid=settings.jwt_key_id,
    )


def decode_access_token(token: str, key_set: JwtKeySet) -> dict[str, Any]:
    """Verify a baked-auth access token and return its claims."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc

    candidates = key_set.verification_keys(header.get("kid"))

    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for key in candidates:
        try:
            payload = jwt.decode(token, key.material, algorithms=[key.algorithm])
            break
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Invalid or expired access token.") from exc
        except jwt.PyJWTError as exc:
            last_error = exc
            continue

    if payload is None:
        raise AuthenticationError("Invalid or expired access token.") from last_error

    if payload.get("typ") != _ACCESS_TOKEN_TYPE or not payload.get("sub"):
        raise AuthenticationError("Invalid or expired access token.")
    return payload


def principal_from_token(token: str, key_set: JwtKeySet) -> AuthPrincipal:
    payload = decode_access_token(token, key_set)
    try:
        auth_id = UUID(str(payload["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc

    roles = payload.get("roles") or []
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise AuthenticationError("Invalid or expired access token.")

    username = payload.get("username")
    if username is not None and not isinstance(username, str):
        raise AuthenticationError("Invalid or expired access token.")

    return AuthPrincipal(auth_id=auth_id, username=username, roles=tuple(roles))
