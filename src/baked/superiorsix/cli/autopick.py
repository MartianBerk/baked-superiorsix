"""CLI entry for automated match slate autopick (SS-229).

Scheduler wiring is out of scope. Uses the same AutopickService as
``POST /admin/autopickmatches``, but with **next-Saturday look-ahead**
(``match_date=None``). The admin API instead scopes to a requested day.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.predictions import SqlAlchemyPredictionRepository
from baked.superiorsix.repositories.rounds import SqlAlchemyRoundRepository
from baked.superiorsix.repositories.subrounds import SqlAlchemySubroundRepository
from baked.superiorsix.repositories.team_xref import SqlAlchemyTeamXrefRepository
from baked.superiorsix.services.autopick import AutopickService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.subrounds import SubroundService
from baked.superiorsix.services.team_xref import TeamXrefService


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_autopick(session) -> AutopickService:
    team_xref = TeamXrefService(SqlAlchemyTeamXrefRepository(session))
    leagues = LeagueService(
        SqlAlchemyLeagueRepository(session),
        SqlAlchemyMatchRepository(session),
        team_xref,
    )
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues, team_xref)
    # CLI has no roster/auth gateways; RoundService only needs the repository for current().
    rounds = RoundService(SqlAlchemyRoundRepository(session))
    subrounds = SubroundService(
        SqlAlchemySubroundRepository(session),
        SqlAlchemyMatchRepository(session),
        rounds,
    )
    predictions = PredictionService(
        SqlAlchemyPredictionRepository(session),
        rounds,
        matches,
    )
    return AutopickService(
        rounds=rounds,
        subrounds=subrounds,
        matches=matches,
        predictions=predictions,
    )


def _result_payload(result) -> dict:
    return {
        "status": result.status,
        "reason": None if result.reason is None else result.reason.value,
        "message": result.message,
        "subround_id": None if result.subround is None else result.subround.id,
        "subround_sequence": None if result.subround is None else result.subround.sequence,
        "match_ids": [match.id for match in result.matches],
        "kickoff": None if result.kickoff is None else result.kickoff.isoformat(),
        "target_difficulty_band": result.target_difficulty_band,
        "average_difficulty_band": result.average_difficulty_band,
    }


async def _run(settings: Settings, *, now: datetime | None) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        autopick = _build_autopick(session)
        result = await autopick.run(now=now)
        await session.commit()
    print(json.dumps(_result_payload(result), indent=2, sort_keys=True))
    # Exit 0 for both selected and expected no-ops (scheduler-friendly).
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Autopick the next Super Six slate of 6 matches (SS-229).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Override current UTC time for gates / next-Saturday (testing)",
    )
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    now: datetime | None = None
    if args.now:
        parsed = datetime.fromisoformat(args.now)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        now = parsed.astimezone(UTC)
    raise SystemExit(asyncio.run(_run(get_settings(), now=now)))


if __name__ == "__main__":
    print(
        "Use the console script: baked-superiorsix-autopick",
        file=sys.stderr,
    )
    raise SystemExit(2)
