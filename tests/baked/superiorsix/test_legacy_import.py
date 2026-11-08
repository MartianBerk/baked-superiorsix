"""Legacy CSV import CLI."""

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from baked.superiorsix.cli.legacy_csv import load_bundle
from baked.superiorsix.cli.legacy_import import load_bundle_into_db, validate_bundle
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.models import LeagueRecord, MatchRecord, PredictionRecord, RoundRecord
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)

FIXTURES = Path(__file__).parent / "fixtures" / "legacy_migration"


@pytest.fixture
def import_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_path = tmp_path / "import.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("SUPERIORSIX_DATABASE_URL", url)
    return Settings(_env_file=None, database_url=url)


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


@pytest.mark.asyncio
async def test_load_bundle_parses_fixtures() -> None:
    bundle = load_bundle(FIXTURES)
    assert bundle.ok, bundle.errors
    assert len(bundle.rounds) == 2
    assert len(bundle.matches) == 3
    assert len(bundle.predictions) == 4
    assert bundle.player_mapping[10] == UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.mark.asyncio
async def test_validate_and_load_round_trip(import_settings: Settings) -> None:
    await _seed_league(import_settings)
    assert await validate_bundle(import_settings, FIXTURES) == 0
    assert (
        await load_bundle_into_db(import_settings, FIXTURES, truncate=True, dry_run=False) == 0
    )

    engine = create_engine(import_settings.database_url)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        rounds = (await session.execute(select(RoundRecord))).scalars().all()
        assert len(rounds) == 2
        open_round = next(row for row in rounds if row.end_date is None)
        assert open_round.id == 2

        match_200 = (
            await session.execute(select(MatchRecord).where(MatchRecord.id == 200))
        ).scalar_one()
        assert match_200.subround_id == 5
        assert match_200.use_match is True
        assert match_200.game_number == 1

        pl_league = (
            await session.execute(select(LeagueRecord).where(LeagueRecord.code == "PL"))
        ).scalar_one()
        match_100 = (
            await session.execute(select(MatchRecord).where(MatchRecord.id == 100))
        ).scalar_one()
        assert match_100.league_id == pl_league.id

        preds = (await session.execute(select(PredictionRecord))).scalars().all()
        assert len(preds) == 4
        assert all(pred.auth_id != UUID(int=0) for pred in preds)


@pytest.mark.asyncio
async def test_validate_fails_on_unmapped_player(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in (
        "player_mapping.csv",
        "league_id_map.csv",
        "rounds.csv",
        "matches.csv",
        "predictions.csv",
        "round_winners.csv",
    ):
        source = (FIXTURES / name).read_text(encoding="utf-8")
        (data_dir / name).write_text(source, encoding="utf-8")
    mapping = data_dir / "player_mapping.csv"
    mapping.write_text(
        "old_player_id,auth_id\n10,aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n",
        encoding="utf-8",
    )
    bundle = load_bundle(data_dir)
    assert not bundle.ok
    assert any("unmapped player_id" in err for err in bundle.errors)
