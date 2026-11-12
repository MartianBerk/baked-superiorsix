"""TEMPORARY SS-212 in-place n_players ALTER + CSV backfill CLI."""

from __future__ import annotations

from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from tests.baked.superiorsix.tokens import TEST_SECRET

from baked.superiorsix.cli import winners_backfill
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.models import LeagueRecord, MatchRecord, RoundRecord
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)


@pytest.fixture
def db_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    db_file = tmp_path / "winners.db"
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+aiosqlite:///{db_file}",
        jwt_secret_key=SecretStr(TEST_SECRET),
        cors_origins=["http://testserver"],
        auth_token_url="http://testserver/v1/auth/token",
    )


def test_parse_backfill_csv_aliases(tmp_path: Path) -> None:
    path = tmp_path / "winners.csv"
    path.write_text(
        "id,players,jackpot\n21,16,19200\n22,14,\n",
        encoding="utf-8",
    )
    rows = winners_backfill.parse_backfill_csv(path)
    assert rows[0].round_id == 21
    assert rows[0].n_players == 16
    assert rows[0].amount_won == 19200
    assert rows[1].round_id == 22
    assert rows[1].n_players == 14
    assert rows[1].amount_won is None


def test_parse_backfill_csv_requires_a_value(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("round_id,n_players,amount_won\n1,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="need n_players"):
        winners_backfill.parse_backfill_csv(path)


@pytest.mark.asyncio
async def test_ensure_adds_n_players_on_existing_table(db_settings: Settings) -> None:
    engine = create_engine(db_settings.database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE rounds ("
                "id INTEGER PRIMARY KEY, "
                "start_date DATE, "
                "end_date DATE, "
                "buy_in_pence INTEGER, "
                "amount_won INTEGER"
                ")"
            )
        )
    added = await winners_backfill.apply_n_players_column(engine)
    assert added is True
    added_again = await winners_backfill.apply_n_players_column(engine)
    assert added_again is False
    async with engine.connect() as connection:
        result = await connection.execute(text("PRAGMA table_info(rounds)"))
        names = {row[1] for row in result.all()}
    assert "n_players" in names
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_updates_closed_round_and_refuses_open(
    db_settings: Settings, tmp_path: Path
) -> None:
    engine = create_engine(db_settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        league = LeagueRecord(
            name="Premier League", code="PL", start_date=date(2025, 8, 1)
        )
        session.add(league)
        await session.flush()
        session.add_all(
            [
                RoundRecord(
                    id=21,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 31),
                    buy_in_pence=200,
                    amount_won=800,
                ),
                RoundRecord(
                    id=22,
                    start_date=date(2026, 2, 1),
                    end_date=None,
                    buy_in_pence=200,
                ),
            ]
        )
        session.add(
            MatchRecord(
                external_id="md-1",
                league_id=league.id,
                match_date=datetime(2026, 1, 10, 15, 0, tzinfo=UTC),
                status="FINISHED",
                home_team="Arsenal",
                away_team="Chelsea",
                use_match=True,
                home_score=1,
                away_score=0,
            )
        )
        await session.commit()

    csv_path = tmp_path / "closed.csv"
    csv_path.write_text("round_id,n_players,amount_won\n21,16,\n", encoding="utf-8")
    rows = winners_backfill.parse_backfill_csv(csv_path)
    async with session_scope(session_factory) as session:
        updated = await winners_backfill.load_backfill(
            session, rows, recompute_amount_won=True, dry_run=False
        )
    assert updated == 1

    async with session_scope(session_factory) as session:
        closed = await session.get(RoundRecord, 21)
        assert closed is not None
        assert closed.n_players == 16
        assert closed.amount_won == 200 * 1 * 16

    open_csv = tmp_path / "open.csv"
    open_csv.write_text("round_id,n_players\n22,16\n", encoding="utf-8")
    open_rows = winners_backfill.parse_backfill_csv(open_csv)
    async with session_scope(session_factory) as session:
        refused = await winners_backfill.load_backfill(
            session, open_rows, recompute_amount_won=False, dry_run=False
        )
    assert refused == -1
    await engine.dispose()


@pytest.mark.asyncio
async def test_load_dry_run_does_not_write(db_settings: Settings, tmp_path: Path) -> None:
    engine = create_engine(db_settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add(
            RoundRecord(
                id=5,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                buy_in_pence=100,
                amount_won=400,
            )
        )
        await session.commit()

    csv_path = tmp_path / "dry.csv"
    csv_path.write_text("round_id,n_players,amount_won\n5,14,2800\n", encoding="utf-8")
    rows = winners_backfill.parse_backfill_csv(csv_path)
    async with session_scope(session_factory) as session:
        updated = await winners_backfill.load_backfill(
            session, rows, recompute_amount_won=False, dry_run=True
        )
    assert updated == 1
    async with session_scope(session_factory) as session:
        record = await session.get(RoundRecord, 5)
        assert record is not None
        assert record.n_players is None
        assert record.amount_won == 400
    await engine.dispose()


def test_main_ensure(db_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import anyio

    engine = create_engine(db_settings.database_url)
    anyio.run(init_db, engine)
    monkeypatch.setattr(winners_backfill, "get_settings", lambda: db_settings)
    buffer = StringIO()
    monkeypatch.setattr(winners_backfill.sys, "stdout", buffer)
    with pytest.raises(SystemExit) as exited:
        winners_backfill.main(["ensure"])
    assert exited.value.code == 0
    assert "n_players" in buffer.getvalue()
