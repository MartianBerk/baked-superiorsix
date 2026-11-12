from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from baked.superiorsix.db.models import Base


def create_engine(database_url: str) -> AsyncEngine:
    kwargs: dict[str, object] = {"echo": False}
    if database_url.endswith(":memory:"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    return create_async_engine(database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


_ROUNDS_INTEGER_COLUMNS = frozenset({"amount_won", "n_players"})


def _ensure_rounds_integer_column(connection: Connection, column_name: str) -> None:
    """ADD COLUMN on an existing ``rounds`` table (create_all does not ALTER)."""
    if column_name not in _ROUNDS_INTEGER_COLUMNS:
        raise ValueError(f"refusing unknown rounds column {column_name!r}")
    inspector = inspect(connection)
    if "rounds" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("rounds")}
    if column_name in columns:
        return
    connection.execute(text(f"ALTER TABLE rounds ADD COLUMN {column_name} INTEGER"))


def _ensure_round_amount_won(connection: Connection) -> None:
    """Add ``rounds.amount_won`` on existing DBs (create_all does not ALTER)."""
    _ensure_rounds_integer_column(connection, "amount_won")


def ensure_round_n_players(connection: Connection) -> None:
    """TEMPORARY (SS-212): add ``rounds.n_players`` in-place.

    Production must not recreate the DB. Remove this helper (and the CLI that
    calls it) after Martin rolls back migration-style persistence.
    """
    _ensure_rounds_integer_column(connection, "n_players")


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_round_amount_won)
        await connection.run_sync(ensure_round_n_players)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
