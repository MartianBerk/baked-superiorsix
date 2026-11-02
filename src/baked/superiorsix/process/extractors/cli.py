from __future__ import annotations

import argparse
import asyncio
import logging
import sys

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
            api_football_key=_secret(settings.api_football_key),
        )
        leagues = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info("league extractor finished count=%s", len(leagues))
    return 0


async def _run_match(settings: Settings, *, league_code: str, match_week: int) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, leagues = _build_services(session)
        extractor = MatchExtractor(
            ingest,
            leagues,
            league_code=league_code,
            match_week=match_week,
            football_data_token=_secret(settings.football_data_api_token),
            api_football_key=_secret(settings.api_football_key),
        )
        matches = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info(
        "match extractor finished league=%s week=%s count=%s",
        league_code,
        match_week,
        len(matches),
    )
    return 0


def league_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract / upsert league catalog (football-data.org + API-Football EL1/EL2).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    raise SystemExit(asyncio.run(_run_league(get_settings())))


def match_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract / upsert fixtures for exactly one league code and one match week. "
            "EL1/EL2 use API-Football; other codes use football-data.org."
        ),
    )
    parser.add_argument(
        "-l",
        "--league",
        required=True,
        help="League code (e.g. PL, ELC, EL1, EL2)",
    )
    parser.add_argument(
        "-w",
        "--match-week",
        type=int,
        required=True,
        dest="match_week",
        help="Exact match week / matchday (not a relative lookahead window)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    raise SystemExit(
        asyncio.run(
            _run_match(
                get_settings(),
                league_code=args.league,
                match_week=args.match_week,
            )
        )
    )


if __name__ == "__main__":
    # Default to match extractor help when invoked as a module without a script entry.
    sys.stderr.write(
        "Use baked-superiorsix-league-extractor or baked-superiorsix-match-extractor.\n"
    )
    raise SystemExit(2)
