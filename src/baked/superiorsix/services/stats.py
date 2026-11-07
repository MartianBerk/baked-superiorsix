"""Player performance aggregates and historic round winners (legacy stats / HISTORIC_ROUNDS)."""

from __future__ import annotations

from dataclasses import dataclass
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
    """One player's scored day (legacy PLAYER_STATS_AGG row)."""

    round_id: int
    auth_id: UUID
    name: str
    match_date: date
    matches: int
    correct: int


@dataclass(frozen=True, slots=True)
class PlayerAggregate:
    """Grouped scores for one player (legacy ``/stats/aggregate`` entry)."""

    auth_id: UUID
    name: str
    scores: list[PlayerDayScore]


@dataclass(frozen=True, slots=True)
class HistoricRound:
    """Closed round summary (legacy HISTORIC_ROUNDS / ``/stats/winners``)."""

    round_id: int
    start_date: date
    end_date: date
    matches: int
    players: int
    jackpot: int
    winner: str
    winner_ids: list[UUID]


def display_name(profile: RosterProfile) -> str:
    """Legacy winner/aggregate label: ``first last`` (nickname ignored for parity)."""
    return f"{profile.first_name} {profile.last_name}".strip() or str(profile.auth_id)


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
        """Finished selected-match scores by player and match day.

        Mirrors legacy ``PLAYER_STATS_AGG``: non-dropped predictions on
        ``use_match`` fixtures with ``FINISHED`` status. Inactive roster
        profiles are excluded (legacy ``retired <> 1``).
        """
        roster = await self._load_roster(bearer_token=bearer_token, active=True)
        by_auth = {profile.auth_id: profile for profile in roster}
        if not by_auth:
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
                PredictionRecord.auth_id.in_(list(by_auth.keys())),
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
        grouped: dict[UUID, PlayerAggregate] = {}
        for round_id, auth_id, match_day_raw, matches, correct in result.all():
            profile = by_auth[auth_id]
            name = display_name(profile)
            day = _as_date(match_day_raw)
            score = PlayerDayScore(
                round_id=int(round_id),
                auth_id=auth_id,
                name=name,
                match_date=day,
                matches=int(matches),
                correct=int(correct),
            )
            existing = grouped.get(auth_id)
            if existing is None:
                grouped[auth_id] = PlayerAggregate(auth_id=auth_id, name=name, scores=[score])
            else:
                existing.scores.append(score)

        return list(grouped.values())

    async def historic_rounds(self, *, bearer_token: str | None = None) -> list[HistoricRound]:
        """Closed rounds with jackpot and winner names (legacy ``HISTORIC_ROUNDS``)."""
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

        players_result = await self._session.execute(
            select(PredictionRecord.round_id, func.count(func.distinct(PredictionRecord.auth_id)))
            .where(PredictionRecord.round_id.in_(round_ids))
            .group_by(PredictionRecord.round_id)
        )
        players_by_round = {int(round_id): int(count) for round_id, count in players_result.all()}

        # Distinct calendar days of selected matches inside each closed round window.
        match_day = func.date(MatchRecord.match_date)
        match_days_by_round: dict[int, int] = {}
        for record in closed:
            assert record.end_date is not None
            start, _ = day_bounds(record.start_date)
            _, end = day_bounds(record.end_date)
            day_count = await self._session.scalar(
                select(func.count(func.distinct(match_day))).where(
                    MatchRecord.use_match.is_(True),
                    MatchRecord.match_date >= start,
                    MatchRecord.match_date < end,
                )
            )
            match_days_by_round[record.id] = int(day_count or 0)

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
            players = players_by_round.get(record.id, 0)
            matches = match_days_by_round.get(record.id, 0)
            historic.append(
                HistoricRound(
                    round_id=record.id,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    matches=matches,
                    players=players,
                    jackpot=record.buy_in_pence * matches * players,
                    winner=" & ".join(winner_labels),
                    winner_ids=winner_ids,
                )
            )
        return historic

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
    # SQLite ``date()`` returns ``YYYY-MM-DD``.
    return date.fromisoformat(text[:10])
