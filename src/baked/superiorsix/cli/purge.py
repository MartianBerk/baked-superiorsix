"""Ops purge CLIs: old matches by league+date, old predictions by round id.

Keeps ``rounds`` / ``round_winners`` / ``amount_won`` so Stats Winners survives.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.models import (
    LeagueRecord,
    MatchRecord,
    PredictionRecord,
    RoundRecord,
    SubroundRecord,
)
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)

_MIN_MATCH_HISTORY = timedelta(days=365 * 2)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from exc


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [yes/N]: ").strip().casefold()
    except EOFError:
        return False
    return answer in {"yes", "y"}


async def _resolve_league_id(session: AsyncSession, league_code: str) -> int | None:
    code = league_code.strip().upper()
    league_id = await session.scalar(
        select(LeagueRecord.id).where(func.upper(LeagueRecord.code) == code)
    )
    return int(league_id) if league_id is not None else None


async def purge_matches(
    session: AsyncSession,
    *,
    league_code: str,
    before_date: date,
    dry_run: bool,
) -> int:
    """Delete unreferenced matches for one league with ``match_date`` before ``before_date``."""
    league_id = await _resolve_league_id(session, league_code)
    if league_id is None:
        print(f"error: unknown league code {league_code!r}", file=sys.stderr)
        return -1

    cutoff = _day_start(before_date)
    referenced = select(PredictionRecord.match_id).distinct()
    league_filter = MatchRecord.league_id == league_id
    candidates = await session.execute(
        select(func.count())
        .select_from(MatchRecord)
        .where(
            league_filter,
            MatchRecord.match_date < cutoff,
            MatchRecord.id.not_in(referenced),
        )
    )
    count = int(candidates.scalar_one() or 0)
    code = league_code.strip().upper()
    print(f"league {code}: matches before {before_date.isoformat()} (unreferenced): {count}")

    remaining_oldest = await session.scalar(
        select(func.min(MatchRecord.match_date)).where(
            league_filter,
            or_(
                MatchRecord.match_date >= cutoff,
                MatchRecord.id.in_(referenced),
            ),
        )
    )
    today = datetime.now(UTC).date()
    history_floor = today - _MIN_MATCH_HISTORY
    if remaining_oldest is None:
        print(
            f"warning: league {code} would have no remaining matches "
            f"(under {_MIN_MATCH_HISTORY.days // 365} years of history)",
            file=sys.stderr,
        )
        short_history = True
    else:
        oldest_day = remaining_oldest.astimezone(UTC).date()
        print(f"league {code}: oldest remaining match_date after purge: {oldest_day.isoformat()}")
        short_history = oldest_day > history_floor
        if short_history:
            print(
                f"warning: remaining history for {code} would start "
                f"{oldest_day.isoformat()} (under 2 years before {today.isoformat()})",
                file=sys.stderr,
            )

    if dry_run or count == 0:
        if dry_run:
            print("dry-run: no changes")
        return count

    if short_history and not _confirm(
        f"Proceed with matches purge for {code} despite under-2-year remaining history?"
    ):
        print("aborted: confirmation declined")
        return 0

    await session.execute(
        delete(MatchRecord).where(
            league_filter,
            MatchRecord.match_date < cutoff,
            MatchRecord.id.not_in(referenced),
        )
    )
    await session.commit()
    print(f"deleted matches: {count}")
    return count


async def _protected_prediction_round_ids(session: AsyncSession) -> tuple[int | None, int | None]:
    """Return ``(current_open_id, previous_closed_id)`` for Performance guards."""
    open_id = await session.scalar(
        select(RoundRecord.id)
        .where(RoundRecord.end_date.is_(None))
        .order_by(RoundRecord.start_date.desc())
        .limit(1)
    )
    previous_id = await session.scalar(
        select(RoundRecord.id)
        .where(RoundRecord.end_date.is_not(None))
        .order_by(RoundRecord.end_date.desc(), RoundRecord.start_date.desc())
        .limit(1)
    )
    return (
        int(open_id) if open_id is not None else None,
        int(previous_id) if previous_id is not None else None,
    )


async def purge_predictions(
    session: AsyncSession,
    *,
    before_round_id: int,
    dry_run: bool,
) -> dict[str, int] | None:
    """Delete predictions (and subrounds) for closed rounds with ``id < before_round_id``.

    Refuses if the purge would touch the open round or the most recently closed
    round (current / current−1) so Performance aggregates stay intact.

    Leaves ``rounds`` + ``round_winners`` + ``amount_won`` intact for Stats Winners.
    """
    current_id, previous_id = await _protected_prediction_round_ids(session)
    print(f"protected rounds: current={current_id!s} previous={previous_id!s}")

    if previous_id is None:
        print(
            "error: no closed round to anchor previous (current−1); refusing prediction purge",
            file=sys.stderr,
        )
        return None

    # id < before_round_id must not include previous (n−1) or current (n).
    max_before = previous_id
    if before_round_id > max_before:
        print(
            f"error: --before-round-id {before_round_id} would purge protected "
            f"previous round {previous_id}"
            + (f" and/or current {current_id}" if current_id is not None else "")
            + f"; use --before-round-id <= {max_before}",
            file=sys.stderr,
        )
        return None

    closed = await session.execute(
        select(RoundRecord.id).where(
            RoundRecord.id < before_round_id,
            RoundRecord.end_date.is_not(None),
            RoundRecord.id != previous_id,
        )
    )
    round_ids = [int(round_id) for round_id in closed.scalars().all()]
    if current_id is not None:
        round_ids = [round_id for round_id in round_ids if round_id != current_id]

    if not round_ids:
        print(f"closed rounds with id < {before_round_id} (excluding protected): 0")
        if dry_run:
            print("dry-run: no changes")
        return {"rounds": 0, "predictions": 0, "subrounds": 0}

    pred_count = int(
        await session.scalar(
            select(func.count())
            .select_from(PredictionRecord)
            .where(PredictionRecord.round_id.in_(round_ids))
        )
        or 0
    )
    sub_ids_result = await session.execute(
        select(SubroundRecord.id).where(SubroundRecord.round_id.in_(round_ids))
    )
    subround_ids = [int(sub_id) for sub_id in sub_ids_result.scalars().all()]
    sub_count = len(subround_ids)

    print(f"closed rounds with id < {before_round_id} (excluding protected): {len(round_ids)}")
    print(f"  predictions: {pred_count}")
    print(f"  subrounds:   {sub_count}")
    if dry_run:
        print("dry-run: no changes")
        return {
            "rounds": len(round_ids),
            "predictions": pred_count,
            "subrounds": sub_count,
        }

    if pred_count:
        await session.execute(
            delete(PredictionRecord).where(PredictionRecord.round_id.in_(round_ids))
        )
    if subround_ids:
        await session.execute(
            update(MatchRecord)
            .where(MatchRecord.subround_id.in_(subround_ids))
            .values(subround_id=None, game_number=None, use_match=False)
        )
        await session.execute(
            delete(SubroundRecord).where(SubroundRecord.id.in_(subround_ids))
        )
    await session.commit()
    print(f"deleted predictions: {pred_count}")
    print(f"deleted subrounds: {sub_count}")
    return {
        "rounds": len(round_ids),
        "predictions": pred_count,
        "subrounds": sub_count,
    }


async def _run_matches(
    settings: Settings,
    *,
    league_code: str,
    before_date: date,
    dry_run: bool,
) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        deleted = await purge_matches(
            session,
            league_code=league_code,
            before_date=before_date,
            dry_run=dry_run,
        )
    return 1 if deleted < 0 else 0


async def _run_predictions(
    settings: Settings, *, before_round_id: int, dry_run: bool
) -> int:
    if before_round_id < 1:
        print("error: --before-round-id must be >= 1", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        result = await purge_predictions(
            session, before_round_id=before_round_id, dry_run=dry_run
        )
    return 0 if result is not None else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Purge old match/prediction rows while keeping closed-round winners "
            "and amount_won for Stats Winners."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    matches = sub.add_parser(
        "matches",
        help="Delete matches for one league before a date (not referenced by predictions)",
    )
    matches.add_argument(
        "--league",
        required=True,
        help="League code to purge (required; never purges all leagues at once)",
    )
    matches.add_argument(
        "--before-date",
        type=_parse_iso_date,
        required=True,
        help="Delete matches with match_date before this UTC date (YYYY-MM-DD)",
    )
    matches.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates without deleting",
    )

    predictions = sub.add_parser(
        "predictions",
        help=(
            "Delete predictions (+ subrounds) for closed rounds with id < N; "
            "refuses current / current−1; keeps rounds/winners/amount_won"
        ),
    )
    predictions.add_argument(
        "--before-round-id",
        type=int,
        required=True,
        help="Purge closed rounds with id strictly less than this value",
    )
    predictions.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates without deleting",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "matches":
        exit_code = asyncio.run(
            _run_matches(
                settings,
                league_code=args.league,
                before_date=args.before_date,
                dry_run=args.dry_run,
            )
        )
    else:
        exit_code = asyncio.run(
            _run_predictions(
                settings,
                before_round_id=args.before_round_id,
                dry_run=args.dry_run,
            )
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
