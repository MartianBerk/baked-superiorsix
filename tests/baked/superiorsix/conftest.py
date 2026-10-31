import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.baked.superiorsix.tokens import TEST_SECRET

from baked.superiorsix.api.app import create_app
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.session import create_engine, create_session_factory


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key=SecretStr(TEST_SECRET),
        cors_origins=["http://testserver"],
        auth_token_url="http://testserver/v1/auth/token",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    app = create_app(settings=settings, session_factory=session_factory, engine=engine)
    with TestClient(app) as test_client:
        yield test_client
