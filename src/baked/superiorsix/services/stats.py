"""Player success-rate aggregates (current + n−1) and full historic winners (SS-18)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import RosterProfile, UserProfileGateway
from baked.superiorsix.core.config import Settings
from baked.superiorsix.db.models import (
    MatchRecord,
    PredictionRecord,
    RoundRecord,
    RoundWinnerRecord,
)
from baked.superiorsix.domain.exceptions import AuthAdminError, RosterError
from baked.superiorsix.domain.models import MatchStatus, PredictionChoice
from baked.superiorsix.repositories.rounds import count_selected_match_days, freeze_amount_won
from baked.superiorsix.services.matches import day_bounds

AggregateScope = Literal["current", "previous"]


@dataclass(frozen=True, slots=True)
class PlayerDayScore:
    """One player's scored match-day within a single round scope."""

    round_id: int
    auth_id: UUID
    name: str
    match_date: date
    matches: int
    correct: int


@dataclass(frozen=True, slots=True)
class PlayerAggregate:
    """Aggregate row with totals + optional day breakdown for one round scope."""

    auth_id: UUID
    name: str
    correct: int
    matches: int
    success_rate: float
    scores: list[PlayerDayScore] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HistoricRound:
    """Closed round winners summary (full multi-round archive)."""

    round_id: int
    start_date: date
    end_date: date
    matches: int
    players: int
    jackpot: int
    winner: str
    winner_ids: list[UUID]


def display_name(profile: RosterProfile) -> str:
    """Aggregate/winner label: ``first last``."""
    return f"{profile.first_name} {profile.last_name}".strip() or str(profile.auth_id)


def success_rate(*, correct: int, matches: int) -> float:
    if matches <= 0:
        return 0.0
    return round((correct / matches) * 100.0, 2)


class StatsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        profiles: UserProfileGateway,
        auth_admin: AuthAdminGateway,
        settings: Settings,
    ) -> None:
        self._session = session
        self._profiles = profiles
        self._auth_admin = auth_admin
        self._settings = settings

    async def aggregate(
        self,
        *,
        scope: AggregateScope = "previous",
        start_date: date | None = None,
        end_date: date | None = None,
        bearer_token: str | None = None,
    ) -> tuple[list[PlayerAggregate], int | None]:
        """Success-rate aggregates for one round scope.

        ``scope=previous`` (default): most recently closed round (n−1).
        ``scope=current``: open round in progress (n), else ``([], None)``.

        Returns ``(rows, round_id)`` so the API can echo which round was used.

        Day ``scores`` are one point per scored match-day (subround slate days
        after import). Optional ``start_date`` / ``end_date`` further filter.

        Known inactive roster profiles are excluded. Predictors present in the
        round data but missing from the roster (common right after a legacy
        import before every ``auth_id`` is invited) are still included, labelled
        with the ``auth_id`` string until a profile exists.
        """
        target = await self._round_for_scope(scope)
        if target is None:
            return [], None

        roster = await self._load_roster(bearer_token=bearer_token, active=None)
        by_auth = {profile.auth_id: profile for profile in roster}
        inactive = {auth_id for auth_id, profile in by_auth.items() if not profile.active}

        day_rows = await self._day_scores(
            auth_ids=None,
            round_ids=[target.id],
            start_date=start_date,
            end_date=end_date,
        )
        grouped: dict[UUID, list[PlayerDayScore]] = {}
        for row in day_rows:
            if row.auth_id in inactive:
                continue
            profile = by_auth.get(row.auth_id)
            name = display_name(profile) if profile is not None else str(row.auth_id)
            score = PlayerDayScore(
                round_id=row.round_id,
                auth_id=row.auth_id,
                name=name,
                match_date=row.match_date,
                matches=row.matches,
                correct=row.correct,
            )
            grouped.setdefault(row.auth_id, []).append(score)

        aggregates: list[PlayerAggregate] = []
        for auth_id, scores in grouped.items():
            profile = by_auth.get(auth_id)
            name = display_name(profile) if profile is not None else str(auth_id)
            correct = sum(item.correct for item in scores)
            matches = sum(item.matches for item in scores)
            aggregates.append(
                PlayerAggregate(
                    auth_id=auth_id,
                    name=name,
                    correct=correct,
                    matches=matches,
                    success_rate=success_rate(correct=correct, matches=matches),
                    scores=scores,
                )
            )
        aggregates.sort(key=lambda row: (-row.success_rate, row.name.lower(), str(row.auth_id)))
        return aggregates, target.id

    async def historic_rounds(self, *, bearer_token: str | None = None) -> list[HistoricRound]:
        """Full closed-round winners archive (legacy HISTORIC_ROUNDS).

        ``players`` is the roster snapshot stored on the round (``n_players``),
        not distinct predictors. ``jackpot`` prefers frozen ``amount_won``.
        """
        roster = await self._load_roster(bearer_token=bearer_token, active=None)
        names = {profile.auth_id: display_name(profile) for profile in roster}

        rounds_result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_not(None))
            .order_by(RoundRecord.start_date.desc())
        )
        closed = list(rounds_result.scalars().all())
        if not closed:
            return []

        round_ids = [record.id for record in closed]

        winners_result = await self._session.execute(
            select(RoundWinnerRecord.round_id, RoundWinnerRecord.auth_id).where(
                RoundWinnerRecord.round_id.in_(round_ids)
            )
        )
        winners_by_round: dict[int, list[UUID]] = {}
        for round_id, auth_id in winners_result.all():
            winners_by_round.setdefault(int(round_id), []).append(auth_id)

        match_days_by_round: dict[int, int] = {}
        for record in closed:
            assert record.end_date is not None
            match_days_by_round[record.id] = await count_selected_match_days(
                self._session,
                start_date=record.start_date,
                end_date=record.end_date,
            )

        historic: list[HistoricRound] = []
        for record in closed:
            assert record.end_date is not None
            winner_ids = sorted(winners_by_round.get(record.id, []), key=str)
            winner_labels = [
                names.get(auth_id) or str(auth_id)
                for auth_id in sorted(
                    winner_ids,
                    key=lambda aid: (names.get(aid) or str(aid)).lower(),
                )
            ]
            players = int(record.n_players or 0)
            matches = match_days_by_round.get(record.id, 0)
            if record.amount_won is not None:
                jackpot = record.amount_won
            else:
                jackpot = freeze_amount_won(
                    buy_in_pence=record.buy_in_pence,
                    match_days=matches,
                    n_players=players,
                )
            historic.append(
                HistoricRound(
                    round_id=record.id,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    matches=matches,
                    players=players,
                    jackpot=jackpot,
                    winner=" & ".join(winner_labels),
                    winner_ids=winner_ids,
                )
            )
        return historic

    async def _day_scores(
        self,
        *,
        auth_ids: list[UUID] | None,
        round_ids: list[int],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[PlayerDayScore]:
        if not round_ids:
            return []
        if auth_ids is not None and not auth_ids:
            return []

        correct_expr = case(
            (
                (PredictionRecord.prediction == PredictionChoice.HOME)
                & (MatchRecord.home_score > MatchRecord.away_score),
                1,
            ),
            (
                (PredictionRecord.prediction == PredictionChoice.AWAY)
                & (MatchRecord.away_score > MatchRecord.home_score),
                1,
            ),
            (
                (PredictionRecord.prediction == PredictionChoice.DRAW)
                & (MatchRecord.home_score == MatchRecord.away_score),
                1,
            ),
            else_=0,
        )
        match_day = func.date(MatchRecord.match_date)
        filters = [
            PredictionRecord.dropped.is_(False),
            MatchRecord.use_match.is_(True),
            MatchRecord.status == MatchStatus.FINISHED,
            PredictionRecord.round_id.in_(round_ids),
        ]
        if auth_ids is not None:
            filters.append(PredictionRecord.auth_id.in_(auth_ids))
        stmt = (
            select(
                PredictionRecord.round_id,
                PredictionRecord.auth_id,
                match_day.label("match_day"),
                func.count(MatchRecord.id).label("matches"),
                func.coalesce(func.sum(correct_expr), 0).label("correct"),
            )
            .join(MatchRecord, MatchRecord.id == PredictionRecord.match_id)
            .where(*filters)
            .group_by(PredictionRecord.round_id, PredictionRecord.auth_id, match_day)
            .order_by(match_day, PredictionRecord.auth_id)
        )
        if start_date is not None:
            start, _ = day_bounds(start_date)
            stmt = stmt.where(MatchRecord.match_date >= start)
        if end_date is not None:
            _, end = day_bounds(end_date)
            stmt = stmt.where(MatchRecord.match_date < end)

        result = await self._session.execute(stmt)
        rows: list[PlayerDayScore] = []
        for round_id, auth_id, match_day_raw, matches, correct in result.all():
            rows.append(
                PlayerDayScore(
                    round_id=int(round_id),
                    auth_id=auth_id,
                    name="",
                    match_date=_as_date(match_day_raw),
                    matches=int(matches),
                    correct=int(correct),
                )
            )
        return rows

    async def _round_for_scope(self, scope: AggregateScope) -> RoundRecord | None:
        if scope == "current":
            return await self._current_open_round()
        return await self._previous_closed_round()

    async def _current_open_round(self) -> RoundRecord | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_(None))
            .order_by(RoundRecord.id.desc())
        )
        return result.scalars().first()

    async def _previous_closed_round(self) -> RoundRecord | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_not(None))
            .order_by(RoundRecord.end_date.desc(), RoundRecord.id.desc())
        )
        return result.scalars().first()

    async def _load_roster(
        self,
        *,
        bearer_token: str | None,
        active: bool | None,
    ) -> list[RosterProfile]:
        token = bearer_token or await self._service_token()
        if token is None:
            return []
        try:
            return await self._profiles.list_profiles(token, active=active)
        except RosterError:
            return []

    async def _service_token(self) -> str | None:
        email = self._settings.auth_admin_email
        password = self._settings.auth_admin_password
        if not email or password is None:
            return None
        try:
            return await self._auth_admin.login_user(
                email=email,
                password=password.get_secret_value(),
                audience=self._settings.jwt_audience,
            )
        except AuthAdminError:
            return None


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    text = str(value)
    return date.fromisoformat(text[:10])
