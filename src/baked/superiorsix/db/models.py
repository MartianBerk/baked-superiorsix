from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeagueRecord(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    start_date: Mapped[date] = mapped_column(Date)
    current_matchday: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("external_id", name="uq_matches_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    matchday: Mapped[int] = mapped_column(Integer)
    match_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    match_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    home_team: Mapped[str] = mapped_column(String(128), index=True)
    away_team: Mapped[str] = mapped_column(String(128), index=True)
    use_match: Mapped[bool] = mapped_column(Boolean, default=False)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RoundRecord(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    buy_in_pence: Mapped[int] = mapped_column(Integer)


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
