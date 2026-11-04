from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

TEST_SECRET = "test-secret-key-must-be-at-least-32b"
TEST_AUDIENCE = "superiorsix"


def make_access_token(
    auth_id: UUID | None = None,
    *,
    secret: str = TEST_SECRET,
    algorithm: str = "HS256",
    kid: str | None = None,
    expires_in_minutes: int = 15,
    roles: list[str] | None = None,
    audience: str = TEST_AUDIENCE,
    **claims: object,
) -> tuple[str, UUID]:
    subject = auth_id or uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "username": "martin",
        "roles": roles if roles is not None else ["player"],
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_in_minutes)).timestamp()),
        "typ": "access",
        **claims,
    }
    headers = {"kid": kid} if kid else None
    token = jwt.encode(payload, secret, algorithm=algorithm, headers=headers)
    return token, subject


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
