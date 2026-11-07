"""Player success-rate aggregates and one-round lookback comparison (SS-18)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
from baked.superiorsix.services.matches import day_bounds


@dataclass(frozen=True, slots=True)
class PlayerDayScore:
    """One player's scored match-day within the lookback window."""

    round_id: int
    auth_id: UUID
    name: str
    match_date: date
    matches: int
    correct: int


@dataclass(frozen=True, slots=True)
class PlayerRate:
    """Computed success rate for one player (FE renders; does not recalculate)."""

    auth_id: UUID
    name: str
    correct: int
    matches: int
    success_rate: float


@dataclass(frozen=True, slots=True)
class PlayerAggregate:
    """Aggregate row with totals + optional day breakdown."""

    auth_id: UUID
    name: str
    correct: int
    matches: int
    success_rate: float
    scores: list[PlayerDayScore] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RoundLookback:
    """Current or previous round snapshot for comparison UI."""

    round_id: int
    start_date: date
    end_date: date | None
    winner: str
    winner_ids: list[UUID]
    stats: list[PlayerRate]


@dataclass(frozen=True, slots=True)
class LookbackComparison:
    """One-round lookback: open/current round vs most recently closed round."""

    current_round: RoundLookback | None
    previous_round: RoundLookback | None


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
        start_date: date | None = None,
        end_date: date | None = None,
        bearer_token: str | None = None,
    ) -> list[PlayerAggregate]:
        """Success-rate aggregates over the one-round lookback window.

        Default window = current open round (if any) + previous closed round.
        Optional ``start_date`` / ``end_date`` further filter match days.
        Inactive roster profiles are excluded.
        """
        roster = await self._load_roster(bearer_token=bearer_token, active=True)
        by_auth = {profile.auth_id: profile for profile in roster}
        if not by_auth:
            return []

        lookback_ids = await self._lookback_round_ids()
        if not lookback_ids:
            return []

        day_rows = await self._day_scores(
            auth_ids=list(by_auth.keys()),
            round_ids=lookback_ids,
            start_date=start_date,
            end_date=end_date,
        )
        grouped: dict[UUID, list[PlayerDayScore]] = {}
        for row in day_rows:
            profile = by_auth[row.auth_id]
            name = display_name(profile)
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
            correct = sum(item.correct for item in scores)
            matches = sum(item.matches for item in scores)
            aggregates.append(
                PlayerAggregate(
                    auth_id=auth_id,
                    name=display_name(by_auth[auth_id]),
                    correct=correct,
                    matches=matches,
                    success_rate=success_rate(correct=correct, matches=matches),
                    scores=scores,
                )
            )
        aggregates.sort(key=lambda row: (-row.success_rate, row.name.lower(), str(row.auth_id)))
        return aggregates

    async def lookback(self, *, bearer_token: str | None = None) -> LookbackComparison:
        """Current vs previous round success rates (not a full historic archive)."""
        roster = await self._load_roster(bearer_token=bearer_token, active=True)
        by_auth = {profile.auth_id: profile for profile in roster}
        names = {profile.auth_id: display_name(profile) for profile in roster}

        current_record = await self._current_round()
        previous_record = await self._previous_closed_round()

        current = None
        if current_record is not None:
            current = await self._round_lookback(
                current_record,
                by_auth=by_auth,
                names=names,
                include_winners=False,
            )

        previous = None
        if previous_record is not None:
            previous = await self._round_lookback(
                previous_record,
                by_auth=by_auth,
                names=names,
                include_winners=True,
            )

        return LookbackComparison(current_round=current, previous_round=previous)

    async def _round_lookback(
        self,
        record: RoundRecord,
        *,
        by_auth: dict[UUID, RosterProfile],
        names: dict[UUID, str],
        include_winners: bool,
    ) -> RoundLookback:
        rates = await self._round_rates(record.id, by_auth=by_auth)
        winner_ids: list[UUID] = []
        winner = ""
        if include_winners:
            winner_ids = await self._winner_ids(record.id)
            winner_labels = [
                names.get(auth_id) or str(auth_id)
                for auth_id in sorted(
                    winner_ids,
                    key=lambda aid: (names.get(aid) or str(aid)).lower(),
                )
            ]
            winner = " & ".join(winner_labels)
        return RoundLookback(
            round_id=record.id,
            start_date=record.start_date,
            end_date=record.end_date,
            winner=winner,
            winner_ids=winner_ids,
            stats=rates,
        )

    async def _round_rates(
        self,
        round_id: int,
        *,
        by_auth: dict[UUID, RosterProfile],
    ) -> list[PlayerRate]:
        if not by_auth:
            return []
        day_rows = await self._day_scores(
            auth_ids=list(by_auth.keys()),
            round_ids=[round_id],
        )
        totals: dict[UUID, list[int]] = {}
        for row in day_rows:
            bucket = totals.setdefault(row.auth_id, [0, 0])
            bucket[0] += row.correct
            bucket[1] += row.matches

        rates: list[PlayerRate] = []
        for auth_id, (correct, matches) in totals.items():
            rates.append(
                PlayerRate(
                    auth_id=auth_id,
                    name=display_name(by_auth[auth_id]),
                    correct=correct,
                    matches=matches,
                    success_rate=success_rate(correct=correct, matches=matches),
                )
            )
        rates.sort(key=lambda row: (-row.success_rate, row.name.lower(), str(row.auth_id)))
        return rates

    async def _day_scores(
        self,
        *,
        auth_ids: list[UUID],
        round_ids: list[int],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[PlayerDayScore]:
        if not auth_ids or not round_ids:
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
        stmt = (
            select(
                PredictionRecord.round_id,
                PredictionRecord.auth_id,
                match_day.label("match_day"),
                func.count(MatchRecord.id).label("matches"),
                func.coalesce(func.sum(correct_expr), 0).label("correct"),
            )
            .join(MatchRecord, MatchRecord.id == PredictionRecord.match_id)
            .where(
                PredictionRecord.dropped.is_(False),
                MatchRecord.use_match.is_(True),
                MatchRecord.status == MatchStatus.FINISHED,
                PredictionRecord.auth_id.in_(auth_ids),
                PredictionRecord.round_id.in_(round_ids),
            )
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

    async def _lookback_round_ids(self) -> list[int]:
        ids: list[int] = []
        current = await self._current_round()
        if current is not None:
            ids.append(current.id)
        previous = await self._previous_closed_round()
        if previous is not None and previous.id not in ids:
            ids.append(previous.id)
        return ids

    async def _current_round(self) -> RoundRecord | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_(None))
            .order_by(RoundRecord.start_date.desc())
        )
        return result.scalars().first()

    async def _previous_closed_round(self) -> RoundRecord | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_not(None))
            .order_by(RoundRecord.end_date.desc(), RoundRecord.id.desc())
        )
        return result.scalars().first()

    async def _winner_ids(self, round_id: int) -> list[UUID]:
        result = await self._session.execute(
            select(RoundWinnerRecord.auth_id)
            .where(RoundWinnerRecord.round_id == round_id)
            .order_by(RoundWinnerRecord.auth_id)
        )
        return list(result.scalars().all())

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
