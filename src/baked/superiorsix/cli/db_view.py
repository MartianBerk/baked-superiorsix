from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime

from sqlalchemy import and_, func, select

from baked.superiorsix.cli.tsv import format_tsv
from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.models import LeagueRecord, MatchRecord
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.services.matches import day_bounds

LEAGUE_HEADERS = ("id", "code", "name", "start_date")

MATCH_HEADERS = (
    "id",
    "league",
    "match_date",
    "status",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "use_match",
    "external_id",
    "last_updated",
)


def _parse_match_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d-%m-%Y").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid match date {value!r}, expected dd-mm-yyyy"
        ) from exc


async def _fetch_leagues(settings: Settings) -> str:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        repo = SqlAlchemyLeagueRepository(session)
        leagues = await repo.list()
    rows = [(league.id, league.code, league.name, league.start_date) for league in leagues]
    return format_tsv(LEAGUE_HEADERS, rows)


async def _fetch_matches(
    settings: Settings,
    *,
    league: str | None,
    match_date: date | None,
    status: str | None,
    home_team: str | None,
    away_team: str | None,
) -> str:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        stmt = (
            select(MatchRecord, LeagueRecord.code)
            .join(LeagueRecord, MatchRecord.league_id == LeagueRecord.id)
            .order_by(MatchRecord.match_date, MatchRecord.id)
        )
        clauses: list = []
        if league is not None:
            clauses.append(func.upper(LeagueRecord.code) == league.upper())
        if match_date is not None:
            start, end = day_bounds(match_date)
            clauses.append(MatchRecord.match_date >= start)
            clauses.append(MatchRecord.match_date < end)
        if status is not None:
            clauses.append(MatchRecord.status == status)
        if home_team is not None:
            needle = home_team.casefold()
            clauses.append(func.lower(MatchRecord.home_team).contains(needle))
        if away_team is not None:
            needle = away_team.casefold()
            clauses.append(func.lower(MatchRecord.away_team).contains(needle))
        if clauses:
            stmt = stmt.where(and_(*clauses))
        result = await session.execute(stmt)
        rows = [
            (
                record.id,
                league_code,
                record.match_date.isoformat(),
                record.status,
                record.home_team,
                record.away_team,
                record.home_score,
                record.away_score,
                record.use_match,
                record.external_id,
                record.last_updated.isoformat(),
            )
            for record, league_code in result.all()
        ]
    return format_tsv(MATCH_HEADERS, rows)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect leagues and matches stored in the Superior Six database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("leagues", help="List all leagues (TSV).")

    matches = sub.add_parser("matches", help="List matches with optional filters (TSV).")
    matches.add_argument(
        "-l",
        "--league",
        metavar="CODE",
        help="League code (e.g. PL, ELC)",
    )
    matches.add_argument(
        "-d",
        "--match-date",
        type=_parse_match_date,
        dest="match_date",
        metavar="DD-MM-YYYY",
        help="Calendar day in UTC (legacy dd-mm-yyyy)",
    )
    matches.add_argument(
        "-s",
        "--status",
        help="Exact status (e.g. SCHEDULED, FINISHED, IN PLAY)",
    )
    matches.add_argument(
        "--home-team",
        help="Case-insensitive substring match on home team name",
    )
    matches.add_argument(
        "--away-team",
        help="Case-insensitive substring match on away team name",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "leagues":
        output = asyncio.run(_fetch_leagues(settings))
    else:
        output = asyncio.run(
            _fetch_matches(
                settings,
                league=args.league,
                match_date=args.match_date,
                status=args.status,
                home_team=args.home_team,
                away_team=args.away_team,
            )
        )
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
