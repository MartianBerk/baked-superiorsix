"""Import legacy Superior Six game data from CSV exports."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from baked.superiorsix.cli.legacy_csv import LegacyCsvBundle, SubroundMatchSlotRow, load_bundle
from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.models import (
    LeagueRecord,
    MatchRecord,
    PredictionRecord,
    RoundRecord,
    RoundWinnerRecord,
    SubroundRecord,
)
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    init_db,
    session_scope,
)

_TABLES_TRUNCATE_ORDER = (
    PredictionRecord,
    RoundWinnerRecord,
    MatchRecord,
    SubroundRecord,
    RoundRecord,
)

_SEQUENCE_TABLES = ("rounds", "subrounds", "matches", "predictions", "leagues")


async def _league_code_to_id(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(LeagueRecord.id, LeagueRecord.code))
    return {code.upper(): int(league_id) for league_id, code in result.all()}


def _validate_league_codes(bundle: LegacyCsvBundle, code_to_id: dict[str, int]) -> list[str]:
    errors: list[str] = []
    needed = set(bundle.league_id_map.values())
    for code in sorted(needed):
        if code not in code_to_id:
            errors.append(f"league code {code!r} not in database — run league extractor first")
    unmapped_leagues = {
        row.old_league_id
        for row in bundle.matches
        if bundle.league_id_map.get(row.old_league_id) not in code_to_id
    }
    for old_id in sorted(unmapped_leagues):
        code = bundle.league_id_map.get(old_id, "?")
        if code not in code_to_id:
            errors.append(f"matches reference old league_id {old_id} ({code}) missing from DB")
    return errors


def _print_summary(bundle: LegacyCsvBundle) -> None:
    print("CSV summary:")
    print(f"  player mappings:     {len(bundle.player_mapping)}")
    print(f"  league id mappings:  {len(bundle.league_id_map)}")
    print(f"  rounds:              {len(bundle.rounds)}")
    print(f"  subrounds:           {len(bundle.subrounds)}")
    print(f"  matches:             {len(bundle.matches)}")
    print(f"  predictions:         {len(bundle.predictions)}")
    print(f"  round_winners:       {len(bundle.round_winners)}")
    print(f"  subround_match_slots:{len(bundle.subround_match_slots)}")


async def validate_bundle(
    settings: Settings,
    data_dir: Path,
    *,
    check_db: bool = True,
) -> int:
    bundle = load_bundle(data_dir)
    if not bundle.ok:
        for message in bundle.errors:
            print(f"error: {message}", file=sys.stderr)
        return 1

    _print_summary(bundle)

    if check_db:
        engine = create_engine(settings.database_url)
        await init_db(engine)
        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            code_to_id = await _league_code_to_id(session)
            db_errors = _validate_league_codes(bundle, code_to_id)
            if not code_to_id:
                db_errors.append("no leagues in database — run league extractor first")
            for message in db_errors:
                print(f"error: {message}", file=sys.stderr)
            if db_errors:
                return 1

    print("validate: OK")
    return 0


async def _truncate_game_tables(session: AsyncSession) -> None:
    for model in _TABLES_TRUNCATE_ORDER:
        await session.execute(delete(model))


def _apply_slots(bundle: LegacyCsvBundle) -> dict[int, SubroundMatchSlotRow]:
    by_match: dict[int, SubroundMatchSlotRow] = {}
    for slot in bundle.subround_match_slots:
        by_match[slot.match_id] = slot
    return by_match


async def _reset_sequences(session: AsyncSession, engine: AsyncEngine) -> None:
    dialect = engine.dialect.name
    if dialect == "sqlite":
        has_sequence_table = await session.scalar(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'sqlite_sequence'"
            )
        )
        if not has_sequence_table:
            return
        for table in _SEQUENCE_TABLES:
            await session.execute(
                text(
                    f"INSERT INTO sqlite_sequence (name, seq) "
                    f"SELECT :name, COALESCE((SELECT MAX(id) FROM {table}), 0) "
                    "ON CONFLICT(name) DO UPDATE SET seq = excluded.seq"
                ),
                {"name": table},
            )
    elif dialect == "postgresql":
        for table in _SEQUENCE_TABLES:
            await session.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                )
            )
    else:
        print(f"note: sequence reset not implemented for dialect {dialect!r}", file=sys.stderr)


async def load_bundle_into_db(
    settings: Settings,
    data_dir: Path,
    *,
    truncate: bool = False,
    dry_run: bool = False,
) -> int:
    bundle = load_bundle(data_dir)
    if not bundle.ok:
        for message in bundle.errors:
            print(f"error: {message}", file=sys.stderr)
        return 1

    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    async with session_scope(session_factory) as session:
        code_to_id = await _league_code_to_id(session)
        db_errors = _validate_league_codes(bundle, code_to_id)
        if not code_to_id:
            db_errors.append("no leagues in database — run league extractor first")
        for message in db_errors:
            print(f"error: {message}", file=sys.stderr)
        if db_errors:
            return 1

        if dry_run:
            _print_summary(bundle)
            print("dry-run: would load rows (no database changes)")
            return 0

        if truncate:
            await _truncate_game_tables(session)

        for row in bundle.rounds:
            session.add(
                RoundRecord(
                    id=row.id,
                    start_date=row.start_date,
                    end_date=row.end_date,
                    buy_in_pence=row.buy_in_pence,
                )
            )

        for row in bundle.subrounds:
            session.add(
                SubroundRecord(
                    id=row.id,
                    round_id=row.round_id,
                    sequence=row.sequence,
                    created_at=row.created_at,
                    closed_at=row.closed_at,
                )
            )

        slot_by_match = _apply_slots(bundle)

        for row in bundle.matches:
            code = bundle.league_id_map[row.old_league_id]
            league_id = code_to_id[code]
            slot = slot_by_match.get(row.id)
            subround_id = row.subround_id
            game_number = row.game_number
            use_match = row.use_match
            if slot is not None:
                subround_id = slot.subround_id
                if slot.game_number is not None:
                    game_number = slot.game_number
                use_match = slot.use_match
            session.add(
                MatchRecord(
                    id=row.id,
                    external_id=row.external_id,
                    league_id=league_id,
                    match_date=row.match_date,
                    match_minute=row.match_minute,
                    status=row.status,
                    home_team=row.home_team,
                    away_team=row.away_team,
                    use_match=use_match,
                    home_score=row.home_score,
                    away_score=row.away_score,
                    game_number=game_number,
                    subround_id=subround_id,
                    difficulty_score=row.difficulty_score,
                    difficulty_band=row.difficulty_band,
                    last_updated=row.last_updated,
                )
            )

        for row in bundle.predictions:
            prediction_kwargs: dict[str, object] = {
                "round_id": row.round_id,
                "auth_id": row.auth_id,
                "match_id": row.match_id,
                "prediction": row.prediction,
                "dropped": row.dropped,
            }
            if row.id is not None:
                prediction_kwargs["id"] = row.id
            session.add(PredictionRecord(**prediction_kwargs))

        for row in bundle.round_winners:
            session.add(
                RoundWinnerRecord(
                    round_id=row.round_id,
                    auth_id=row.auth_id,
                )
            )

        await session.flush()
        await _reset_sequences(session, engine)
        await session.commit()

    counts = await _count_rows(engine)
    _print_summary(bundle)
    print("load: OK")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


async def _count_rows(engine: AsyncEngine) -> dict[str, int]:
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        rounds = await session.scalar(select(func.count()).select_from(RoundRecord))
        subrounds = await session.scalar(select(func.count()).select_from(SubroundRecord))
        matches = await session.scalar(select(func.count()).select_from(MatchRecord))
        predictions = await session.scalar(select(func.count()).select_from(PredictionRecord))
        winners = await session.scalar(select(func.count()).select_from(RoundWinnerRecord))
        return {
            "rounds": int(rounds or 0),
            "subrounds": int(subrounds or 0),
            "matches": int(matches or 0),
            "predictions": int(predictions or 0),
            "round_winners": int(winners or 0),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or load legacy Superior Six CSV exports into baked-superiorsix. "
            "Run league extractors before load. After load, verify with "
            "baked-superiorsix-db and GET /stats/winners, /stats/aggregate."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("migration-data"),
        help="Directory containing migration CSV files (default: ./migration-data)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate",
        help="Parse CSVs and check referential integrity (optional DB league check)",
    )
    validate.add_argument(
        "--skip-db",
        action="store_true",
        help="Only validate CSV files without checking leagues in the database",
    )

    load = sub.add_parser("load", help="Import validated CSV rows into the game database")
    load.add_argument(
        "--truncate",
        action="store_true",
        help="Delete game tables (not leagues) before import",
    )
    load.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print summary without writing to the database",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    data_dir: Path = args.data_dir
    if not data_dir.is_dir():
        print(f"error: data directory not found: {data_dir}", file=sys.stderr)
        raise SystemExit(1)

    if args.command == "validate":
        exit_code = asyncio.run(
            validate_bundle(settings, data_dir, check_db=not args.skip_db)
        )
    else:
        exit_code = asyncio.run(
            load_bundle_into_db(
                settings,
                data_dir,
                truncate=args.truncate,
                dry_run=args.dry_run,
            )
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
