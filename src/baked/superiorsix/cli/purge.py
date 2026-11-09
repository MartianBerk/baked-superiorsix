"""Ops purge CLIs: old matches by date, old predictions by round id.

Keeps ``rounds`` / ``round_winners`` / ``amount_won`` so Stats Winners survives.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, time

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.models import (
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


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from exc


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


async def purge_matches(
    session: AsyncSession,
    *,
    before_date: date,
    dry_run: bool,
) -> int:
    """Delete matches with ``match_date`` before ``before_date`` not referenced by predictions."""
    cutoff = _day_start(before_date)
    referenced = select(PredictionRecord.match_id).distinct()
    candidates = await session.execute(
        select(func.count())
        .select_from(MatchRecord)
        .where(
            MatchRecord.match_date < cutoff,
            MatchRecord.id.not_in(referenced),
        )
    )
    count = int(candidates.scalar_one() or 0)
    print(f"matches before {before_date.isoformat()} (unreferenced): {count}")
    if dry_run or count == 0:
        if dry_run:
            print("dry-run: no changes")
        return count

    await session.execute(
        delete(MatchRecord).where(
            MatchRecord.match_date < cutoff,
            MatchRecord.id.not_in(referenced),
        )
    )
    await session.commit()
    print(f"deleted matches: {count}")
    return count


async def purge_predictions(
    session: AsyncSession,
    *,
    before_round_id: int,
    dry_run: bool,
) -> dict[str, int]:
    """Delete predictions (and subrounds) for closed rounds with ``id < before_round_id``.

    Leaves ``rounds`` + ``round_winners`` + ``amount_won`` intact for Stats Winners.
    Clears ``matches.subround_id`` / ``game_number`` pointing at deleted subrounds.
    """
    closed = await session.execute(
        select(RoundRecord.id).where(
            RoundRecord.id < before_round_id,
            RoundRecord.end_date.is_not(None),
        )
    )
    round_ids = [int(round_id) for round_id in closed.scalars().all()]
    if not round_ids:
        print(f"closed rounds with id < {before_round_id}: 0")
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

    print(f"closed rounds with id < {before_round_id}: {len(round_ids)}")
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


async def _run_matches(settings: Settings, *, before_date: date, dry_run: bool) -> int:
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        await purge_matches(session, before_date=before_date, dry_run=dry_run)
    return 0


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
        await purge_predictions(
            session, before_round_id=before_round_id, dry_run=dry_run
        )
    return 0


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
        help="Delete matches before a date that are not referenced by predictions",
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
            "keeps rounds/winners/amount_won"
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
            _run_matches(settings, before_date=args.before_date, dry_run=args.dry_run)
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
