"""TEMPORARY (SS-212): in-place ``rounds.n_players`` ALTER + CSV backfill.

Does not drop or recreate the database. Delete this module, its
``baked-superiorsix-winners-backfill`` script entry, and
``ensure_round_n_players`` in ``db/session.py`` after Martin rolls back
migration-style persistence (the ``n_players`` column on the model stays).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from baked.superiorsix.cli.legacy_csv import parse_int, parse_optional_int, pick, read_csv
from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.db.models import RoundRecord
from baked.superiorsix.db.session import (
    create_engine,
    create_session_factory,
    ensure_round_n_players,
    session_scope,
)
from baked.superiorsix.repositories.rounds import count_selected_match_days, freeze_amount_won

CSV_COLUMNS = """CSV columns (header row required; names are case-insensitive):

  round_id     required. Aliases: id, round
  n_players    optional. Aliases: players
  amount_won   optional integer pence. Aliases: jackpot, prize_pot_pence

At least one of n_players / amount_won is required per row. Blank amount_won
leaves the stored pot unchanged unless --recompute-amount-won is set (then
amount_won = buy_in_pence × selected match-days × n_players).

Example:
  round_id,n_players,amount_won
  21,16,19200
  22,16,
"""


@dataclass(frozen=True, slots=True)
class BackfillRow:
    round_id: int
    n_players: int | None
    amount_won: int | None


def parse_backfill_csv(path: Path) -> list[BackfillRow]:
    rows = read_csv(path)
    parsed: list[BackfillRow] = []
    for index, raw in enumerate(rows, start=2):
        round_raw = pick(raw, "round_id", "id", "round")
        try:
            round_id = parse_int(round_raw, label="round_id")
        except ValueError as exc:
            raise ValueError(f"{path.name} line {index}: {exc}") from exc
        try:
            n_players = parse_optional_int(pick(raw, "n_players", "players"))
            amount_won = parse_optional_int(
                pick(raw, "amount_won", "jackpot", "prize_pot_pence")
            )
        except ValueError as exc:
            raise ValueError(f"{path.name} line {index}: {exc}") from exc
        if n_players is None and amount_won is None:
            raise ValueError(
                f"{path.name} line {index}: need n_players and/or amount_won"
            )
        if n_players is not None and n_players < 0:
            raise ValueError(f"{path.name} line {index}: n_players must be >= 0")
        if amount_won is not None and amount_won < 0:
            raise ValueError(f"{path.name} line {index}: amount_won must be >= 0")
        parsed.append(
            BackfillRow(round_id=round_id, n_players=n_players, amount_won=amount_won)
        )
    return parsed


async def apply_n_players_column(engine: AsyncEngine) -> bool:
    """ALTER TABLE ADD COLUMN n_players when missing. Returns True if added."""
    added = False

    def _apply(connection) -> None:
        nonlocal added
        inspector = inspect(connection)
        existing: set[str] = set()
        if "rounds" in inspector.get_table_names():
            existing = {column["name"] for column in inspector.get_columns("rounds")}
        ensure_round_n_players(connection)
        if "n_players" not in existing and "rounds" in inspector.get_table_names():
            added = True

    async with engine.begin() as connection:
        await connection.run_sync(_apply)
    return added


async def load_backfill(
    session: AsyncSession,
    rows: list[BackfillRow],
    *,
    recompute_amount_won: bool,
    dry_run: bool,
) -> int:
    """Apply CSV rows to closed rounds. Returns the number of rounds updated."""
    updated = 0
    for row in rows:
        record = await session.get(RoundRecord, row.round_id)
        if record is None:
            print(f"error: round {row.round_id} not found", file=sys.stderr)
            return -1
        if record.end_date is None:
            print(
                f"error: round {row.round_id} is still open; refuse backfill",
                file=sys.stderr,
            )
            return -1

        new_n_players = record.n_players if row.n_players is None else row.n_players
        if row.amount_won is not None:
            new_amount = row.amount_won
        elif recompute_amount_won:
            if new_n_players is None:
                print(
                    f"error: round {row.round_id}: --recompute-amount-won needs n_players",
                    file=sys.stderr,
                )
                return -1
            match_days = await count_selected_match_days(
                session,
                start_date=record.start_date,
                end_date=record.end_date,
            )
            new_amount = freeze_amount_won(
                buy_in_pence=record.buy_in_pence,
                match_days=match_days,
                n_players=new_n_players,
            )
        else:
            new_amount = record.amount_won

        print(
            f"round {row.round_id}: n_players {record.n_players} -> {new_n_players}; "
            f"amount_won {record.amount_won} -> {new_amount}"
        )
        if not dry_run:
            record.n_players = new_n_players
            record.amount_won = new_amount
        updated += 1

    if dry_run:
        print(f"dry-run: would update {updated} round(s)")
        return updated

    await session.commit()
    print(f"updated {updated} round(s)")
    return updated


async def _run_ensure(settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    added = await apply_n_players_column(engine)
    print("added rounds.n_players" if added else "rounds.n_players already present")
    await engine.dispose()
    return 0


async def _run_load(
    settings: Settings,
    csv_path: Path,
    *,
    recompute_amount_won: bool,
    dry_run: bool,
) -> int:
    if not csv_path.is_file():
        print(f"error: CSV not found: {csv_path}", file=sys.stderr)
        return 1
    try:
        rows = parse_backfill_csv(csv_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("error: CSV has no data rows", file=sys.stderr)
        return 1

    engine = create_engine(settings.database_url)
    await apply_n_players_column(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        updated = await load_backfill(
            session,
            rows,
            recompute_amount_won=recompute_amount_won,
            dry_run=dry_run,
        )
    await engine.dispose()
    return 0 if updated >= 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "TEMPORARY SS-212: ALTER rounds.n_players in-place and/or backfill "
            "closed-round roster counts and jackpots from CSV. Does not recreate "
            "the database."
        ),
        epilog=CSV_COLUMNS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "ensure",
        help="ALTER TABLE rounds ADD COLUMN n_players INTEGER if missing",
    )

    load = sub.add_parser(
        "load",
        help="Upsert n_players / amount_won on closed rounds from CSV (runs ensure first)",
    )
    load.add_argument(
        "--csv",
        required=True,
        type=Path,
        help="CSV path (see --help for columns)",
    )
    load.add_argument(
        "--recompute-amount-won",
        action="store_true",
        help=(
            "When amount_won is blank, set it to buy_in × match-days × n_players "
            "(overwrites a previously frozen pot)"
        ),
    )
    load.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print planned updates without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "ensure":
        exit_code = asyncio.run(_run_ensure(settings))
    else:
        exit_code = asyncio.run(
            _run_load(
                settings,
                args.csv,
                recompute_amount_won=args.recompute_amount_won,
                dry_run=args.dry_run,
            )
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
