from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class LeagueRecord(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    start_date: Mapped[date] = mapped_column(Date)


class RoundRecord(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    buy_in_pence: Mapped[int] = mapped_column(Integer)


class RoundWinnerRecord(Base):
    """Winners for a closed round — keyed by roster ``auth_id`` (not legacy player id)."""

    __tablename__ = "round_winners"

    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), primary_key=True)
    auth_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)


class SubroundRecord(Base):
    """Admin match-submission slate under a parent ``rounds`` row."""

    __tablename__ = "subrounds"
    __table_args__ = (
        UniqueConstraint("round_id", "sequence", name="uq_subrounds_round_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("external_id", name="uq_matches_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    match_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    match_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    home_team: Mapped[str] = mapped_column(String(128), index=True)
    away_team: Mapped[str] = mapped_column(String(128), index=True)
    use_match: Mapped[bool] = mapped_column(Boolean, default=False)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subround_id: Mapped[int | None] = mapped_column(
        ForeignKey("subrounds.id"),
        nullable=True,
        index=True,
    )
    # Frozen once first computed (SS-125); later ingest / recalc must not overwrite.
    difficulty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty_band: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )


class PredictionRecord(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "round_id",
            "auth_id",
            "match_id",
            name="uq_predictions_round_auth_match",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    auth_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    prediction: Mapped[str] = mapped_column(String(16))
    dropped: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Euro Wizard (SS-19). Isolated from Super Six + World Cup tables. ---


class EurosTournamentRecord(Base):
    """One Euros edition: static buy-in pot (no round rollover)."""

    __tablename__ = "euros_tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competition_code: Mapped[str] = mapped_column(String(16), default="EC", index=True)
    label: Mapped[str] = mapped_column(String(128), default="European Championship")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    buy_in_pence: Mapped[int] = mapped_column(Integer)
    # Frozen at open: buy_in × entrant_count = prize pot for the whole tournament.
    entrant_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )


class EurosMatchRecord(Base):
    """Euro Wizard fixtures — not the Super Six ``matches`` table."""

    __tablename__ = "euros_matches"
    __table_args__ = (UniqueConstraint("external_id", name="uq_euros_matches_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    matchday: Mapped[int] = mapped_column(Integer, index=True)
    match_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    match_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    home_team: Mapped[str] = mapped_column(String(128), index=True)
    away_team: Mapped[str] = mapped_column(String(128), index=True)
    use_match: Mapped[bool] = mapped_column(Boolean, default=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_time: Mapped[bool] = mapped_column(Boolean, default=False)
    penalties: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )


class EurosPredictionRecord(Base):
    """Euro Wizard picks keyed by roster ``auth_id`` (not Super Six predictions)."""

    __tablename__ = "euros_predictions"
    __table_args__ = (
        UniqueConstraint(
            "auth_id",
            "match_id",
            name="uq_euros_predictions_auth_match",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auth_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("euros_matches.id"), index=True)
    prediction: Mapped[str] = mapped_column(String(16))
    plus_ninety: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_time: Mapped[bool] = mapped_column(Boolean, default=False)
    penalties: Mapped[bool] = mapped_column(Boolean, default=False)
    dropped: Mapped[bool] = mapped_column(Boolean, default=False)
