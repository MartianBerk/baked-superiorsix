"""Legacy CSV import → Stats Performance / Winners (SS-155)."""

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.baked.superiorsix.fakes import FakeAuthAdminClient, FakeUserProfileClient
from tests.baked.superiorsix.tokens import TEST_SECRET

from baked.superiorsix.api.app import create_app
from baked.superiorsix.cli.legacy_csv import load_bundle
from baked.superiorsix.cli.legacy_import import load_bundle_into_db
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.models import LeagueRecord
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)

FIXTURES = Path(__file__).parent / "fixtures" / "legacy_migration"
ADA = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BOB = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture
def import_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_path = tmp_path / "import.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("SUPERIORSIX_DATABASE_URL", url)
    return Settings(
        _env_file=None,
        environment="test",
        database_url=url,
        jwt_secret_key=SecretStr(TEST_SECRET),
        jwt_audience="superiorsix",
        cors_origins=["http://testserver"],
        auth_token_url="http://testserver/v1/auth/token",
        user_service_url="http://testserver-user",
        auth_admin_email="ops@example.com",
        auth_admin_password=SecretStr("local-dev-change-me"),
    )


async def _seed_league(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add(
            LeagueRecord(
                name="Premier League",
                code="PL",
                start_date=date(2024, 8, 1),
            )
        )
        await session.commit()


def _client(
    settings: Settings,
    *,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> TestClient:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        engine=engine,
        user_profiles=user_profiles,
        auth_admin=auth_admin,
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_stats_after_import_with_matching_roster(import_settings: Settings) -> None:
    await _seed_league(import_settings)
    assert await load_bundle_into_db(import_settings, FIXTURES, truncate=True) == 0

    user_profiles = FakeUserProfileClient()
    user_profiles.seed(ADA, first_name="Ada", last_name="Lovelace")
    user_profiles.seed(BOB, first_name="Bob", last_name="Builder")
    auth_admin = FakeAuthAdminClient()
    auth_admin.register("ops@example.com", "local-dev-change-me")

    with _client(import_settings, user_profiles=user_profiles, auth_admin=auth_admin) as client:
        aggregate = client.get("/stats/aggregate")
        assert aggregate.status_code == 200, aggregate.text
        by_player = {row["playerid"]: row for row in aggregate.json()["stats"]}
        assert set(by_player) == {str(ADA), str(BOB)}
        assert by_player[str(ADA)]["correct"] == 2
        assert by_player[str(ADA)]["success_rate"] == 100.0
        assert by_player[str(BOB)]["success_rate"] == 0.0

        winners = client.get("/stats/winners")
        assert winners.status_code == 200, winners.text
        rounds = winners.json()["rounds"]
        assert len(rounds) == 1
        assert rounds[0]["round_id"] == 1
        assert rounds[0]["winner"] == "Ada Lovelace"
        assert rounds[0]["matches"] == 2
        assert rounds[0]["players"] == 2
        assert rounds[0]["jackpot"] == 500 * 2 * 2


@pytest.mark.asyncio
async def test_stats_aggregate_includes_imported_auth_ids_missing_from_roster(
    import_settings: Settings,
) -> None:
    """After import, Performance still yields rates before every auth_id is rostered."""
    await _seed_league(import_settings)
    assert await load_bundle_into_db(import_settings, FIXTURES, truncate=True) == 0

    user_profiles = FakeUserProfileClient()
    # Unrelated roster player — import auth_ids are absent from profiles.
    user_profiles.seed(
        UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        first_name="Other",
        last_name="Player",
    )
    auth_admin = FakeAuthAdminClient()
    auth_admin.register("ops@example.com", "local-dev-change-me")

    with _client(import_settings, user_profiles=user_profiles, auth_admin=auth_admin) as client:
        aggregate = client.get("/stats/aggregate")
        assert aggregate.status_code == 200, aggregate.text
        by_player = {row["playerid"]: row for row in aggregate.json()["stats"]}
        assert set(by_player) == {str(ADA), str(BOB)}
        assert by_player[str(ADA)]["name"] == str(ADA)
        assert by_player[str(ADA)]["success_rate"] == 100.0

        winners = client.get("/stats/winners")
        assert winners.status_code == 200, winners.text
        rounds = winners.json()["rounds"]
        assert len(rounds) == 1
        # Names fall back to auth_id strings when not on roster.
        assert str(ADA) in rounds[0]["winner"]


@pytest.mark.asyncio
async def test_stats_aggregate_excludes_known_inactive_after_import(
    import_settings: Settings,
) -> None:
    await _seed_league(import_settings)
    assert await load_bundle_into_db(import_settings, FIXTURES, truncate=True) == 0

    user_profiles = FakeUserProfileClient()
    user_profiles.seed(ADA, first_name="Ada", last_name="Lovelace")
    bob = user_profiles.seed(BOB, first_name="Bob", last_name="Builder")
    # Mark Bob inactive on the roster document.
    user_profiles._by_auth[BOB] = type(bob)(
        id=bob.id,
        auth_id=bob.auth_id,
        first_name=bob.first_name,
        last_name=bob.last_name,
        nickname=bob.nickname,
        email=bob.email,
        avatar_url=bob.avatar_url,
        active=False,
        profile={**bob.profile, "active": False},
    )
    auth_admin = FakeAuthAdminClient()
    auth_admin.register("ops@example.com", "local-dev-change-me")

    with _client(import_settings, user_profiles=user_profiles, auth_admin=auth_admin) as client:
        aggregate = client.get("/stats/aggregate")
        assert aggregate.status_code == 200, aggregate.text
        by_player = {row["playerid"]: row for row in aggregate.json()["stats"]}
        assert set(by_player) == {str(ADA)}
        assert by_player[str(ADA)]["name"] == "Ada Lovelace"


def test_legacy_predictions_csv_accepts_drop_column(tmp_path: Path) -> None:
    """Legacy SQLite column is ``drop``; importer must map it to ``dropped``."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in (
        "player_mapping.csv",
        "league_id_map.csv",
        "rounds.csv",
        "matches.csv",
        "round_winners.csv",
    ):
        source = (FIXTURES / name).read_text(encoding="utf-8")
        (data_dir / name).write_text(source, encoding="utf-8")
    (data_dir / "predictions.csv").write_text(
        "id,round_id,player_id,match_id,prediction,drop\n"
        "1,1,10,100,home,0\n"
        "2,1,10,101,draw,1\n"
        "3,1,11,100,away,0\n"
        "4,1,11,101,home,0\n",
        encoding="utf-8",
    )
    bundle = load_bundle(data_dir)
    assert bundle.ok, bundle.errors
    by_id = {row.id: row for row in bundle.predictions}
    assert by_id[1].dropped is False
    assert by_id[2].dropped is True
    assert by_id[3].dropped is False
