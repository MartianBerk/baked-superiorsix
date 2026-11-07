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
from baked.superiorsix.process.extractors.auto_score import AutoScoreExtractor
from baked.superiorsix.process.extractors.connectors.football_data import (
    TOURNAMENT_CATALOG_CODES,
)
from baked.superiorsix.process.extractors.league import LeagueExtractor
from baked.superiorsix.process.extractors.match import MatchExtractor
from baked.superiorsix.process.extractors.tournament import TournamentExtractor
from baked.superiorsix.repositories.euros import (
    SqlAlchemyEurosMatchRepository,
    SqlAlchemyEurosPredictionRepository,
    SqlAlchemyEurosTournamentRepository,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.euros import EurosService
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
    euros = EurosService(
        SqlAlchemyEurosTournamentRepository(session),
        SqlAlchemyEurosMatchRepository(session),
        SqlAlchemyEurosPredictionRepository(session),
    )
    return ingest, leagues, matches, euros


async def _run_league(settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, _, _, _ = _build_services(session)
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
        ingest, leagues, _, _ = _build_services(session)
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


async def _run_tournament_catalog(
    settings: Settings,
    *,
    codes: frozenset[str],
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, leagues, _, euros = _build_services(session)
        extractor = TournamentExtractor(
            ingest,
            leagues,
            football_data_token=_secret(settings.football_data_api_token),
            goal_api_key=_secret(settings.goal_api_key),
            codes=codes,
            euros=euros,
        )
        upserted = await extractor.process_catalog()
        await session.commit()
    logging.getLogger(__name__).info(
        "tournament catalog finished codes=%s count=%s",
        ",".join(sorted(codes)),
        len(upserted),
    )
    return 0


async def _run_tournament_matches(
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
        ingest, leagues, _, euros = _build_services(session)
        extractor = TournamentExtractor(
            ingest,
            leagues,
            football_data_token=_secret(settings.football_data_api_token),
            goal_api_key=_secret(settings.goal_api_key),
            euros=euros,
        )
        matches = await extractor.process_matches(
            league_code=league_code,
            date_from=date_from,
            date_to=date_to,
            live=live,
        )
        await session.commit()
    logging.getLogger(__name__).info(
        "tournament match extractor finished league=%s live=%s from=%s to=%s count=%s",
        league_code,
        live,
        date_from.isoformat(),
        date_to.isoformat(),
        len(matches),
    )
    return 0


async def _run_auto_score(
    settings: Settings,
    *,
    use_match_only: bool,
    max_run_seconds: int,
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, leagues, matches, _ = _build_services(session)
        extractor = AutoScoreExtractor(
            ingest,
            leagues,
            matches,
            football_data_token=_secret(settings.football_data_api_token),
            goal_api_key=_secret(settings.goal_api_key),
            use_match_only=use_match_only,
            max_run_seconds=max_run_seconds,
        )
        stats = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info(
        "auto-score finished candidates=%s due=%s skipped=%s leagues_pulled=%s "
        "matches_upserted=%s passes=%s",
        stats["candidates"],
        stats["due"],
        stats["skipped"],
        stats["leagues_pulled"],
        stats["matches_upserted"],
        stats["passes"],
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
            "football-data status=LIVE)."
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
            "football-data.org uses status=LIVE (IN_PLAY + PAUSED)."
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


def tournament_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Tournament / cup ingest via football-data.org (FAC, EC, WC). "
            "Default: upsert tournament league catalog. "
            "With -l/--from (or --live): pull fixtures — FAC/WC → Super Six "
            "matches; EC → isolated euros_matches (SS-19). No Flashscore / Selenium."
        ),
    )
    parser.add_argument(
        "-c",
        "--codes",
        nargs="+",
        metavar="CODE",
        help=(
            "Tournament codes for catalog mode "
            f"(default: {' '.join(sorted(TOURNAMENT_CATALOG_CODES))})"
        ),
    )
    parser.add_argument(
        "-l",
        "--league",
        help="Tournament code for fixture pull (FAC, EC, or WC)",
    )
    parser.add_argument(
        "--from",
        type=_parse_iso_date,
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="Inclusive start date (UTC). Required with -l unless --live.",
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
        help="Pull live/in-play tournament fixtures (football-data status=LIVE).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.league or args.live or args.date_from is not None:
        if not args.league:
            parser.error("-l/--league is required for tournament fixture pull")
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
        raise SystemExit(
            asyncio.run(
                _run_tournament_matches(
                    get_settings(),
                    league_code=args.league,
                    date_from=date_from,
                    date_to=date_to,
                    live=args.live,
                )
            )
        )

    codes = frozenset(code.strip().upper() for code in (args.codes or TOURNAMENT_CATALOG_CODES))
    unknown = codes - TOURNAMENT_CATALOG_CODES
    if unknown:
        parser.error(
            "unsupported tournament codes: "
            + ", ".join(sorted(unknown))
            + f" (supported: {', '.join(sorted(TOURNAMENT_CATALOG_CODES))})"
        )
    raise SystemExit(asyncio.run(_run_tournament_catalog(get_settings(), codes=codes)))


def auto_score_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Smart auto-score job. Finds scoreable fixtures in the live-candidate "
            "window (1h before → 4h after kickoff; default: Super Six selections), "
            "skips API pulls until match_date + active_offset (default +2 minutes) / "
            "during half-time pause / within the 2-minute recheck window, then reuses "
            "MatchExtractor --live per due league. Orchestration may schedule this "
            "~every 15 minutes; this CLI does not install cron/K8s."
        ),
    )
    parser.add_argument(
        "--all-fixtures",
        action="store_true",
        help="Consider all non-finished fixtures in the window (not only use_match).",
    )
    parser.add_argument(
        "-s",
        "--max-run-seconds",
        type=int,
        default=0,
        help=(
            "Optional loop budget (legacy auto-score). 0 = single pass "
            "(default; suitable for external ~15m scheduling)."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.max_run_seconds < 0:
        parser.error("--max-run-seconds must be >= 0")
    _configure_logging(args.verbose)
    raise SystemExit(
        asyncio.run(
            _run_auto_score(
                get_settings(),
                use_match_only=not args.all_fixtures,
                max_run_seconds=args.max_run_seconds,
            )
        )
    )


if __name__ == "__main__":
    sys.stderr.write(
        "Use baked-superiorsix-league-extractor, "
        "baked-superiorsix-match-extractor, "
        "baked-superiorsix-tournament-extractor, or "
        "baked-superiorsix-auto-score.\n"
    )
    raise SystemExit(2)
