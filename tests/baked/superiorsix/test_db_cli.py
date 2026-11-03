from __future__ import annotations

import os
from datetime import UTC, date, datetime
from io import StringIO

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.tokens import TEST_SECRET

from baked.superiorsix.cli import db_view
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.models import LeagueRecord, MatchRecord
from baked.superiorsix.db.session import create_engine, create_session_factory, init_db


@pytest.fixture
def db_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(os.environ):
        if key.startswith("SUPERIORSIX_"):
            monkeypatch.delenv(key, raising=False)
    db_file = tmp_path / "cli.db"
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+aiosqlite:///{db_file}",
        jwt_secret_key=SecretStr(TEST_SECRET),
        cors_origins=["http://testserver"],
        auth_token_url="http://testserver/v1/auth/token",
    )


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with session_factory() as session:
        pl = LeagueRecord(
            name="Premier League",
            code="PL",
            start_date=date(2025, 8, 1),
        )
        elc = LeagueRecord(
            name="Championship",
            code="ELC",
            start_date=date(2025, 8, 1),
        )
        session.add_all([pl, elc])
        await session.flush()
        session.add_all(
            [
                MatchRecord(
                    external_id="pl-1",
                    league_id=pl.id,
                    match_date=datetime(2026, 3, 14, 15, 0, tzinfo=UTC),
                    status="SCHEDULED",
                    home_team="Arsenal FC",
                    away_team="Chelsea FC",
                ),
                MatchRecord(
                    external_id="pl-2",
                    league_id=pl.id,
                    match_date=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
                    status="FINISHED",
                    home_team="Liverpool FC",
                    away_team="Everton FC",
                    home_score=2,
                    away_score=1,
                    use_match=True,
                ),
                MatchRecord(
                    external_id="elc-1",
                    league_id=elc.id,
                    match_date=datetime(2026, 3, 14, 19, 45, tzinfo=UTC),
                    status="SCHEDULED",
                    home_team="Leeds United FC",
                    away_team="Sheffield United FC",
                ),
            ]
        )
        await session.commit()
        return pl.id, elc.id


@pytest.mark.asyncio
async def test_fetch_leagues_tsv(db_settings: Settings) -> None:
    engine = create_engine(db_settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    await _seed(session_factory)

    output = await db_view._fetch_leagues(db_settings)

    assert output.splitlines()[0] == "\t".join(db_view.LEAGUE_HEADERS)
    assert "PL\tPremier League" in output
    assert "ELC\tChampionship" in output


@pytest.mark.asyncio
async def test_fetch_matches_filters(db_settings: Settings) -> None:
    engine = create_engine(db_settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    await _seed(session_factory)

    by_league = await db_view._fetch_matches(
        db_settings,
        league="PL",
        match_date=None,
        status=None,
        home_team=None,
        away_team=None,
    )
    assert by_league.count("\n") == 3  # header + 2 PL rows

    by_date = await db_view._fetch_matches(
        db_settings,
        league=None,
        match_date=date(2026, 3, 14),
        status=None,
        home_team=None,
        away_team=None,
    )
    assert "Arsenal FC" in by_date
    assert "Leeds United FC" in by_date
    assert "Liverpool FC" not in by_date

    by_team = await db_view._fetch_matches(
        db_settings,
        league=None,
        match_date=None,
        status=None,
        home_team="arsenal",
        away_team=None,
    )
    assert "Arsenal FC" in by_team
    assert "Liverpool FC" not in by_team


def test_main_writes_tsv(db_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(db_settings.database_url)
    import anyio

    anyio.run(init_db, engine)
    session_factory = create_session_factory(engine)
    anyio.run(_seed, session_factory)

    buffer = StringIO()
    monkeypatch.setattr(db_view.sys, "stdout", buffer)
    monkeypatch.setattr(db_view, "get_settings", lambda: db_settings)
    db_view.main(["leagues"])

    lines = buffer.getvalue().splitlines()
    assert lines[0] == "\t".join(db_view.LEAGUE_HEADERS)
    assert any("PL" in line for line in lines[1:])
