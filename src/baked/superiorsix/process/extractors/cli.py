from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, date, datetime

from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from baked.superiorsix.process.extractors.league import LeagueExtractor
from baked.superiorsix.process.extractors.match import MatchExtractor
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _secret(value) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    text = text.strip()
    return text or None


def _parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def _build_services(session):
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    ingest = MatchIngestService(matches, leagues)
    return ingest, leagues


async def _run_league(settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, _ = _build_services(session)
        extractor = LeagueExtractor(
            ingest,
            football_data_token=_secret(settings.football_data_api_token),
        )
        leagues = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info("league extractor finished count=%s", len(leagues))
    return 0


async def _run_match(
    settings: Settings,
    *,
    league_code: str,
    date_from: date,
    date_to: date,
    live: bool,
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, leagues = _build_services(session)
        extractor = MatchExtractor(
            ingest,
            leagues,
            league_code=league_code,
            date_from=date_from,
            date_to=date_to,
            football_data_token=_secret(settings.football_data_api_token),
            goal_api_key=_secret(settings.goal_api_key),
            live=live,
        )
        matches = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info(
        "match extractor finished league=%s live=%s from=%s to=%s count=%s",
        league_code,
        live,
        date_from.isoformat(),
        date_to.isoformat(),
        len(matches),
    )
    return 0


def league_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract / upsert league catalog from football-data.org v2.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    raise SystemExit(asyncio.run(_run_league(get_settings())))


def match_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract / upsert fixtures for exactly one league code. "
            "EL1/EL2 use Goal API (one request per date, with pagination); "
            "other codes use football-data.org. "
            "Pass --live for in-play fixtures (Goal API /fixtures/live; "
            "football-data approximates with today's date)."
        ),
    )
    parser.add_argument(
        "-l",
        "--league",
        required=True,
        help="League code (e.g. PL, ELC, EL1, EL2)",
    )
    parser.add_argument(
        "--from",
        type=_parse_iso_date,
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="Inclusive start date (UTC). Required unless --live.",
    )
    parser.add_argument(
        "--to",
        type=_parse_iso_date,
        dest="date_to",
        metavar="YYYY-MM-DD",
        help="Inclusive end date (UTC); defaults to --from",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Pull live/in-play fixtures. Goal API uses /fixtures/live; "
            "football-data.org uses today's UTC date only."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.live:
        today = datetime.now(UTC).date()
        date_from = date_to = today
    else:
        if args.date_from is None:
            parser.error("--from is required unless --live is set")
        date_from = args.date_from
        date_to = args.date_to or args.date_from
        if date_to < date_from:
            parser.error("--to must be on or after --from")
    _configure_logging(args.verbose)
    raise SystemExit(
        asyncio.run(
            _run_match(
                get_settings(),
                league_code=args.league,
                date_from=date_from,
                date_to=date_to,
                live=args.live,
            )
        )
    )


if __name__ == "__main__":
    # Default to match extractor help when invoked as a module without a script entry.
    sys.stderr.write(
        "Use baked-superiorsix-league-extractor or baked-superiorsix-match-extractor.\n"
    )
    raise SystemExit(2)
