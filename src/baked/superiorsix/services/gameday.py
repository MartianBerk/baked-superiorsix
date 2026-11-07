"""Gameday read models: header meta (round / pot) and live scoreboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import RosterProfile, UserProfileGateway
from baked.superiorsix.core.config import Settings
from baked.superiorsix.domain.exceptions import AuthAdminError, RosterError, SubroundNotFoundError
from baked.superiorsix.domain.models import Match, MatchStatus, Prediction, PredictionChoice, Round
from baked.superiorsix.services.matches import MatchService, day_bounds
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.subrounds import SubroundService, SubroundStatus

_LIVE_STATUSES = frozenset({MatchStatus.IN_PLAY, MatchStatus.HALF_TIME})
_NOT_STARTED = frozenset({MatchStatus.SCHEDULED, MatchStatus.POSTPONED})


@dataclass(frozen=True, slots=True)
class GamedayMeta:
    """Header + navigation payload for the player shell (SS-16)."""

    round_number: int
    prize_pot_pence: int
    buy_in_pence: int
    n_players: int
    round_: Round | None
    latest_subround: SubroundStatus | None
    subrounds: list[SubroundStatus] = field(default_factory=list)
    match_days: list[datetime] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PublicCurrentRound:
    """Public current-round summary (legacy ``CURRENT_ROUND`` + Martin pot rules)."""

    round_id: int
    start_date: date
    round_number: int
    buy_in_pence: int
    players: int
    jackpot: int
    prize_pot_pence: int
    subround_id: int | None
    current_match_date: datetime | None
    matches: int


@dataclass(frozen=True, slots=True)
class LiveScoreMatchPick:
    match_id: int
    game_number: int | None
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    status: str
    match_date: datetime
    match_minute: int | None
    prediction: str | None
    correct: bool


@dataclass(frozen=True, slots=True)
class LiveScorePlayer:
    auth_id: UUID
    id: UUID
    name: str
    first_name: str
    last_name: str
    nickname: str | None
    score: int
    matches: list[LiveScoreMatchPick]


@dataclass(frozen=True, slots=True)
class LiveScoresBoard:
    scores: list[LiveScorePlayer]
    subround: SubroundStatus | None
    matches: list[Match]
    match_count: int
    live: bool
    min_kickoff: datetime | None
    max_kickoff: datetime | None
    next_kickoff: datetime | None


class GamedayService:
    def __init__(
        self,
        *,
        rounds: RoundService,
        subrounds: SubroundService,
        matches: MatchService,
        predictions: PredictionService,
        profiles: UserProfileGateway,
        auth_admin: AuthAdminGateway,
        settings: Settings,
    ) -> None:
        self._rounds = rounds
        self._subrounds = subrounds
        self._matches = matches
        self._predictions = predictions
        self._profiles = profiles
        self._auth_admin = auth_admin
        self._settings = settings

    async def meta(self, *, now: datetime | None = None) -> GamedayMeta:
        clock = now or datetime.now(UTC)
        round_ = await self._rounds.current_or_none()
        roster = await self._load_active_roster()
        n_players = len(roster)
        if round_ is None or round_.id is None:
            return GamedayMeta(
                round_number=0,
                prize_pot_pence=0,
                buy_in_pence=0,
                n_players=n_players,
                round_=None,
                latest_subround=None,
            )

        statuses = await self._subrounds.list_for_round(round_.id, now=clock)
        latest = statuses[-1] if statuses else None
        round_number = latest.subround.sequence if latest is not None else 0
        pot = prize_pot_pence(
            buy_in_pence=round_.buy_in_pence,
            n_players=n_players,
            round_number=round_number,
        )
        match_days = await self._match_days_for_round(round_.id)
        return GamedayMeta(
            round_number=round_number,
            prize_pot_pence=pot,
            buy_in_pence=round_.buy_in_pence,
            n_players=n_players,
            round_=round_,
            latest_subround=latest,
            subrounds=statuses,
            match_days=match_days,
        )

    async def current_round_summary(
        self,
        *,
        now: datetime | None = None,
    ) -> PublicCurrentRound | None:
        clock = now or datetime.now(UTC)
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return None

        roster = await self._load_active_roster()
        n_players = len(roster)
        latest = await self._subrounds.latest_status_for_round(round_.id, now=clock)
        round_number = latest.subround.sequence if latest is not None else 0
        pot = prize_pot_pence(
            buy_in_pence=round_.buy_in_pence,
            n_players=n_players,
            round_number=round_number,
        )
        current_match_date = None
        if latest is not None:
            current_match_date = latest.min_kickoff or latest.max_kickoff
        return PublicCurrentRound(
            round_id=round_.id,
            start_date=round_.start_date,
            round_number=round_number,
            buy_in_pence=round_.buy_in_pence,
            players=n_players,
            jackpot=pot,
            prize_pot_pence=pot,
            subround_id=None if latest is None else latest.subround.id,
            current_match_date=current_match_date,
            matches=round_number,
        )

    async def live_scores(
        self,
        *,
        match_date: date | None = None,
        subround_id: int | None = None,
        now: datetime | None = None,
    ) -> LiveScoresBoard:
        clock = now or datetime.now(UTC)
        status_, matches = await self._resolve_slate(
            match_date=match_date,
            subround_id=subround_id,
            now=clock,
        )
        roster = await self._load_active_roster()
        round_ = await self._rounds.current_or_none()
        predictions: list[Prediction] = []
        if round_ is not None and round_.id is not None and matches:
            predictions = await self._predictions.list_active_for_matches(
                round_id=round_.id,
                match_ids=[match.id for match in matches if match.id is not None],
            )
        return build_live_scores_board(
            roster=roster,
            matches=matches,
            predictions=predictions,
            subround=status_,
            now=clock,
        )

    async def _resolve_slate(
        self,
        *,
        match_date: date | None,
        subround_id: int | None,
        now: datetime,
    ) -> tuple[SubroundStatus | None, list[Match]]:
        if subround_id is not None:
            try:
                status_ = await self._subrounds.status_for_id(subround_id, now=now)
            except SubroundNotFoundError:
                raise
            matches = await self._matches.selected_matches_for_subround(subround_id)
            return status_, _sorted_slate(matches)

        if match_date is not None:
            matches = await self._matches.available_matches(match_date)
            subround_ids = {match.subround_id for match in matches if match.subround_id is not None}
            status_: SubroundStatus | None = None
            if len(subround_ids) == 1:
                only_id = next(iter(subround_ids))
                status_ = await self._subrounds.status_for_id(only_id, now=now)
            return status_, _sorted_slate(matches)

        status_ = await self._subrounds.latest_for_current_round(now=now)
        if status_ is None or status_.subround.id is None:
            return None, []
        matches = await self._matches.selected_matches_for_subround(status_.subround.id)
        return status_, _sorted_slate(matches)

    async def _match_days_for_round(self, round_id: int) -> list[datetime]:
        statuses = await self._subrounds.list_for_round(round_id)
        seen: set[date] = set()
        days: list[datetime] = []
        for status_ in statuses:
            assert status_.subround.id is not None
            selected = await self._matches.selected_matches_for_subround(status_.subround.id)
            for match in selected:
                day = _as_utc(match.match_date).date()
                if day in seen:
                    continue
                seen.add(day)
                start, _ = day_bounds(day)
                days.append(start)
        days.sort()
        return days

    async def _load_active_roster(self) -> list[RosterProfile]:
        email = self._settings.auth_admin_email
        password = self._settings.auth_admin_password
        if not email or password is None:
            return []
        try:
            token = await self._auth_admin.login_user(
                email=email,
                password=password.get_secret_value(),
                audience=self._settings.jwt_audience,
            )
            return await self._profiles.list_profiles(token, active=True)
        except (AuthAdminError, RosterError):
            return []


def prize_pot_pence(*, buy_in_pence: int, n_players: int, round_number: int) -> int:
    """``buy_in_pence * full_roster_count * subround_number`` (everyone paid-in).

    Slate growth is 1× / 2× / 3× of ``(buy_in × n_players)``.
    """
    if buy_in_pence < 0 or n_players < 0 or round_number < 0:
        return 0
    return buy_in_pence * n_players * round_number


def prediction_is_correct(match: Match, prediction: str) -> bool:
    if match.home_score is None or match.away_score is None:
        return False
    if match.home_score > match.away_score:
        return prediction == PredictionChoice.HOME
    if match.away_score > match.home_score:
        return prediction == PredictionChoice.AWAY
    return prediction == PredictionChoice.DRAW


def build_live_scores_board(
    *,
    roster: list[RosterProfile],
    matches: list[Match],
    predictions: list[Prediction],
    subround: SubroundStatus | None,
    now: datetime,
) -> LiveScoresBoard:
    slate = _sorted_slate(matches)
    by_auth: dict[UUID, dict[int, Prediction]] = {}
    for prediction in predictions:
        by_auth.setdefault(prediction.auth_id, {})[prediction.match_id] = prediction

    scores: list[LiveScorePlayer] = []
    for profile in sorted(
        roster,
        key=lambda item: (
            (item.last_name or "").lower(),
            (item.first_name or "").lower(),
            str(item.auth_id),
        ),
    ):
        picks = by_auth.get(profile.auth_id, {})
        player_matches: list[LiveScoreMatchPick] = []
        correct_count = 0
        for match in slate:
            assert match.id is not None
            prediction = picks.get(match.id)
            choice = None if prediction is None else prediction.prediction
            correct = False if choice is None else prediction_is_correct(match, choice)
            if correct:
                correct_count += 1
            player_matches.append(
                LiveScoreMatchPick(
                    match_id=match.id,
                    game_number=match.game_number,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    home_score=match.home_score,
                    away_score=match.away_score,
                    status=match.status,
                    match_date=match.match_date,
                    match_minute=match.match_minute,
                    prediction=choice,
                    correct=correct,
                )
            )
        display = profile.nickname or f"{profile.first_name} {profile.last_name}".strip()
        scores.append(
            LiveScorePlayer(
                auth_id=profile.auth_id,
                id=profile.id,
                name=display,
                first_name=profile.first_name,
                last_name=profile.last_name,
                nickname=profile.nickname,
                score=correct_count,
                matches=player_matches,
            )
        )

    scores.sort(key=lambda player: (-player.score, player.name.lower(), str(player.auth_id)))
    kickoffs = [_as_utc(match.match_date) for match in slate]
    live = any(match.status in _LIVE_STATUSES for match in slate)
    next_kickoff = _next_kickoff(slate, now)
    return LiveScoresBoard(
        scores=scores,
        subround=subround,
        matches=slate,
        match_count=len(slate),
        live=live,
        min_kickoff=min(kickoffs) if kickoffs else None,
        max_kickoff=max(kickoffs) if kickoffs else None,
        next_kickoff=next_kickoff,
    )


def _next_kickoff(matches: list[Match], now: datetime) -> datetime | None:
    clock = _as_utc(now)
    candidates: list[datetime] = []
    for match in matches:
        if match.status not in _NOT_STARTED:
            continue
        kickoff = _as_utc(match.match_date)
        if kickoff >= clock:
            candidates.append(kickoff)
    return min(candidates) if candidates else None


def _sorted_slate(matches: list[Match]) -> list[Match]:
    return sorted(
        matches,
        key=lambda match: (
            match.game_number is None,
            match.game_number or 0,
            match.match_date,
            match.id or 0,
        ),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
