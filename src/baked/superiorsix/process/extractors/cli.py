from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, date, datetime, timedelta

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
from baked.superiorsix.process.extractors.worldcup import WorldcupExtractor
from baked.superiorsix.repositories.euros import (
    SqlAlchemyEurosMatchRepository,
    SqlAlchemyEurosPredictionRepository,
    SqlAlchemyEurosTournamentRepository,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.worldcup_matches import SqlAlchemyWorldcupMatchRepository
from baked.superiorsix.services.euros import EurosService
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.worldcup_ingest import WorldcupIngestService


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


DEFAULT_MATCH_EXTRACTOR_DAYS_AHEAD = 7


def resolve_match_extractor_date_range(
    *,
    date_from: date | None,
    date_to: date | None,
    days_ahead: int | None,
    earliest_match_date: date | None,
    today_utc: date,
) -> tuple[date, date, bool]:
    """Resolve inclusive UTC date bounds for the match extractor.

    Returns ``(date_from, date_to, used_today_fallback)`` where the fallback flag
    is set when ``date_from`` was omitted and no actionable (non-finished, non-postponed)
    matches exist for the league. When ``date_from`` is omitted, the anchor is the
    earliest actionable ``match_date`` in the DB for the league (not the latest).
    """
    used_today_fallback = False
    resolved_from = date_from
    if resolved_from is None:
        if earliest_match_date is not None:
            resolved_from = earliest_match_date
        else:
            resolved_from = today_utc
            used_today_fallback = True
    if date_to is not None:
        if date_to < resolved_from:
            raise ValueError("--to must be on or after --from")
        return resolved_from, date_to, used_today_fallback
    span = days_ahead if days_ahead is not None else DEFAULT_MATCH_EXTRACTOR_DAYS_AHEAD
    return resolved_from, resolved_from + timedelta(days=span), used_today_fallback


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
    date_from: date | None,
    date_to: date | None,
    days_ahead: int | None,
    live: bool,
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        ingest, leagues, _, _ = _build_services(session)
        match_repo = SqlAlchemyMatchRepository(session)
        if live:
            today = datetime.now(UTC).date()
            resolved_from = resolved_to = today
            used_today_fallback = False
        else:
            earliest = await match_repo.min_match_date_for_league_code(league_code)
            today = datetime.now(UTC).date()
            try:
                resolved_from, resolved_to, used_today_fallback = resolve_match_extractor_date_range(
                    date_from=date_from,
                    date_to=date_to,
                    days_ahead=days_ahead,
                    earliest_match_date=earliest,
                    today_utc=today,
                )
            except ValueError as exc:
                logging.getLogger(__name__).error("%s", exc)
                return 2
            if used_today_fallback:
                logging.getLogger(__name__).warning(
                    "No actionable matches in DB for league %s; using today UTC %s as --from",
                    league_code,
                    today.isoformat(),
                )
        extractor = MatchExtractor(
            ingest,
            leagues,
            league_code=league_code,
            date_from=resolved_from,
            date_to=resolved_to,
            football_data_token=_secret(settings.football_data_api_token),
            goal_api_key=_secret(settings.goal_api_key),
            live=live,
        )
        pulled = await extractor.process()
        await session.commit()
    logging.getLogger(__name__).info(
        "match extractor finished league=%s live=%s from=%s to=%s count=%s",
        league_code,
        live,
        resolved_from.isoformat(),
        resolved_to.isoformat(),
        len(pulled),
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
        help=(
            "Inclusive start date (UTC). When omitted (and not --live), defaults to "
            "the earliest match_date among actionable fixtures (not finished or postponed) "
            "for this league in the DB, or today UTC with a warning if none."
        ),
    )
    end_group = parser.add_mutually_exclusive_group()
    end_group.add_argument(
        "--to",
        type=_parse_iso_date,
        dest="date_to",
        metavar="YYYY-MM-DD",
        help="Inclusive end date (UTC). Mutually exclusive with --days-ahead.",
    )
    end_group.add_argument(
        "--days-ahead",
        type=int,
        dest="days_ahead",
        metavar="N",
        help=(
            "Inclusive end date is --from + N days (default 7 when --to is omitted). "
            "Mutually exclusive with --to."
        ),
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
    if args.days_ahead is not None and args.days_ahead < 0:
        parser.error("--days-ahead must be zero or positive")
    _configure_logging(args.verbose)
    raise SystemExit(
        asyncio.run(
            _run_match(
                get_settings(),
                league_code=args.league,
                date_from=None if args.live else args.date_from,
                date_to=None if args.live else args.date_to,
                days_ahead=None if args.live else args.days_ahead,
                live=args.live,
            )
        )
    )


def tournament_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Tournament / cup ingest via football-data.org (FAC, EC, WC). "
            "Default: upsert tournament league catalog. "
            "With -l/--from (or --live): FAC → Super Six matches; "
            "EC → isolated euros_matches (SS-19). "
            "WC side-game fixtures use baked-superiorsix-worldcup-extractor. "
            "No Flashscore / Selenium."
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


async def _run_worldcup_catalog(settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        _, leagues, _, _ = _build_services(session)
        wc_ingest = WorldcupIngestService(
            SqlAlchemyWorldcupMatchRepository(session),
            leagues,
        )
        extractor = WorldcupExtractor(
            wc_ingest,
            leagues,
            football_data_token=_secret(settings.football_data_api_token),
        )
        league = await extractor.process_catalog()
        await session.commit()
    logging.getLogger(__name__).info(
        "worldcup catalog finished code=%s",
        None if league is None else league.code,
    )
    return 0


async def _run_worldcup_matches(
    settings: Settings,
    *,
    date_from: date,
    date_to: date,
    live: bool,
    matchday: int | None,
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        _, leagues, _, _ = _build_services(session)
        wc_ingest = WorldcupIngestService(
            SqlAlchemyWorldcupMatchRepository(session),
            leagues,
        )
        extractor = WorldcupExtractor(
            wc_ingest,
            leagues,
            football_data_token=_secret(settings.football_data_api_token),
            matchday_override=matchday,
        )
        matches = await extractor.process_matches(
            date_from=date_from,
            date_to=date_to,
            live=live,
        )
        await session.commit()
    logging.getLogger(__name__).info(
        "worldcup match extractor finished live=%s from=%s to=%s count=%s",
        live,
        date_from.isoformat(),
        date_to.isoformat(),
        len(matches),
    )
    return 0


def worldcup_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "World Cup side-game ingest via football-data.org (code WC). "
            "Writes worldcup_matches only — never Super Six matches or euros_*. "
            "Default: upsert WC into shared leagues catalog. "
            "With --from (or --live): pull fixtures into worldcup_matches."
        ),
    )
    parser.add_argument(
        "--from",
        type=_parse_iso_date,
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="Inclusive start date (UTC). Required for fixture pull unless --live.",
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
        help="Pull live/in-play WC fixtures (football-data status=LIVE).",
    )
    parser.add_argument(
        "--matchday",
        type=int,
        help="Override matchday when football-data omits it (legacy LAST_32 quirk).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.live or args.date_from is not None:
        if args.live:
            today = datetime.now(UTC).date()
            date_from = date_to = today
        else:
            date_from = args.date_from
            date_to = args.date_to or args.date_from
            if date_to < date_from:
                parser.error("--to must be on or after --from")
        raise SystemExit(
            asyncio.run(
                _run_worldcup_matches(
                    get_settings(),
                    date_from=date_from,
                    date_to=date_to,
                    live=args.live,
                    matchday=args.matchday,
                )
            )
        )

    raise SystemExit(asyncio.run(_run_worldcup_catalog(get_settings())))


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
        "baked-superiorsix-tournament-extractor, "
        "baked-superiorsix-worldcup-extractor, or "
        "baked-superiorsix-auto-score.\n"
    )
    raise SystemExit(2)
