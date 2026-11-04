import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.baked.superiorsix.fakes import FakeUserProfileClient
from tests.baked.superiorsix.tokens import TEST_SECRET

from baked.superiorsix.api.app import create_app
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.session import create_engine, create_session_factory


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Keep tests hermetic even if the local shell has invalid SUPERIORSIX_* values.
    for key in list(os.environ):
        if key.startswith("SUPERIORSIX_"):
            monkeypatch.delenv(key, raising=False)
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key=SecretStr(TEST_SECRET),
        jwt_audience="superiorsix",
        cors_origins=["http://testserver"],
        auth_token_url="http://testserver/v1/auth/token",
        user_service_url="http://testserver-user",
    )


@pytest.fixture
def user_profiles() -> FakeUserProfileClient:
    return FakeUserProfileClient()


@pytest.fixture
def client(settings: Settings, user_profiles: FakeUserProfileClient) -> TestClient:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        engine=engine,
        user_profiles=user_profiles,
    )
    with TestClient(app) as test_client:
        yield test_client
