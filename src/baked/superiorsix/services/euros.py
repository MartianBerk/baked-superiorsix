"""Euro Wizard game service — matches, predictions, scores, static pot."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import RosterProfile, UserProfileGateway
from baked.superiorsix.domain.euros_scoring import score_prediction
from baked.superiorsix.domain.exceptions import (
    AuthAdminError,
    EurosMatchNotFoundError,
    EurosPredictionError,
    EurosTournamentError,
    EurosTournamentNotFoundError,
    RosterError,
)
from baked.superiorsix.domain.match_status import coalesce_forward_status, is_status_regression
from baked.superiorsix.domain.models import (
    EurosMatch,
    EurosPrediction,
    EurosScoreRow,
    EurosTournament,
    PredictionChoice,
)
from baked.superiorsix.repositories.euros import (
    EurosMatchRepository,
    EurosPredictionRepository,
    EurosTournamentRepository,
)
from baked.superiorsix.services.side_game_roles import grant_side_game_role

_CUTOFF = timedelta(minutes=10)
_USER_CHOICES = {PredictionChoice.HOME, PredictionChoice.AWAY, PredictionChoice.DRAW}
_CHOICE_ALIASES = {
    "h": PredictionChoice.HOME,
    "home": PredictionChoice.HOME,
    "a": PredictionChoice.AWAY,
    "away": PredictionChoice.AWAY,
    "d": PredictionChoice.DRAW,
    "draw": PredictionChoice.DRAW,
}
EUROS_ROLE = "euros"


@dataclass(frozen=True, slots=True)
class EurosMeta:
    """Header pot for Euro Wizard (static; no round rollover)."""

    tournament: EurosTournament | None
    prize_pot_pence: int
    buy_in_pence: int
    entrant_count: int
    open: bool


class EurosService:
    def __init__(
        self,
        tournaments: EurosTournamentRepository,
        matches: EurosMatchRepository,
        predictions: EurosPredictionRepository,
        *,
        profiles: UserProfileGateway | None = None,
        auth_admin: AuthAdminGateway | None = None,
    ) -> None:
        self._tournaments = tournaments
        self._matches = matches
        self._predictions = predictions
        self._profiles = profiles
        self._auth_admin = auth_admin

    async def meta(self) -> EurosMeta:
        tournament = await self._tournaments.current()
        if tournament is None:
            return EurosMeta(
                tournament=None,
                prize_pot_pence=0,
                buy_in_pence=0,
                entrant_count=0,
                open=False,
            )
        return EurosMeta(
            tournament=tournament,
            prize_pot_pence=tournament.buy_in_pence * tournament.entrant_count,
            buy_in_pence=tournament.buy_in_pence,
            entrant_count=tournament.entrant_count,
            open=tournament.end_date is None,
        )

    async def open_tournament(
        self,
        *,
        start_date: date,
        buy_in_pence: int,
        bearer_token: str,
        entrant_ids: list[UUID],
        label: str = "European Championship",
        competition_code: str = "EC",
    ) -> EurosTournament:
        if buy_in_pence < 0:
            raise EurosTournamentError("buy_in_pence must be >= 0.")
        if not entrant_ids:
            raise EurosTournamentError("entrant_ids must not be empty.")
        existing = await self._tournaments.current()
        if existing is not None:
            raise EurosTournamentError("An open Euros tournament already exists.")
        if self._profiles is None or self._auth_admin is None:
            raise EurosTournamentError(
                "Roster and auth admin gateways are required to open a tournament."
            )
        try:
            granted = await grant_side_game_role(
                entrant_ids=entrant_ids,
                role=EUROS_ROLE,
                bearer_token=bearer_token,
                profiles=self._profiles,
                auth_admin=self._auth_admin,
            )
        except ValueError as exc:
            raise EurosTournamentError(str(exc)) from exc
        except AuthAdminError as exc:
            raise EurosTournamentError(str(exc)) from exc
        except RosterError as exc:
            raise EurosTournamentError(str(exc)) from exc
        return await self._tournaments.add(
            EurosTournament(
                start_date=start_date,
                buy_in_pence=buy_in_pence,
                label=label,
                competition_code=competition_code.strip().upper() or "EC",
                entrant_count=len(granted),
            )
        )

    async def end_tournament(self, *, end_date: date | None = None) -> EurosTournament:
        tournament = await self._tournaments.current()
        if tournament is None or tournament.id is None:
            raise EurosTournamentNotFoundError("No open Euros tournament.")
        tournament.end_date = end_date or date.today()
        return await self._tournaments.save(tournament)

    async def list_tournaments(self) -> list[EurosTournament]:
        return await self._tournaments.list_all()

    async def list_matches(self) -> list[EurosMatch]:
        return await self._matches.list_all()

    async def get_match(self, match_id: int) -> EurosMatch:
        match = await self._matches.get(match_id)
        if match is None:
            raise EurosMatchNotFoundError(f"Euros match {match_id} not found.")
        return match

    async def upsert_fixture(self, match: EurosMatch) -> EurosMatch:
        """Ingest create/update by ``external_id`` with forward-only status."""
        existing = await self._matches.get_by_external_id(match.external_id)
        if existing is None:
            return await self._matches.add(match)

        match.id = existing.id
        match.use_match = existing.use_match
        incoming_status = match.status
        if is_status_regression(existing.status, incoming_status):
            match.status = existing.status
            if match.home_score is None:
                match.home_score = existing.home_score
            if match.away_score is None:
                match.away_score = existing.away_score
            if match.match_minute is None:
                match.match_minute = existing.match_minute
        else:
            match.status = coalesce_forward_status(existing.status, incoming_status)
        return await self._matches.save(match)

    async def apply_score_update(
        self,
        *,
        external_id: str | None = None,
        match_id: int | None = None,
        status: str,
        home_score: int | None = None,
        away_score: int | None = None,
        match_minute: int | None = None,
        extra_time: bool | None = None,
        penalties: bool | None = None,
        matchday: int | None = None,
    ) -> EurosMatch:
        match: EurosMatch | None = None
        if match_id is not None:
            match = await self._matches.get(match_id)
        elif external_id is not None:
            match = await self._matches.get_by_external_id(external_id)
        if match is None or match.id is None:
            raise EurosMatchNotFoundError("Euros match not found for score update.")

        if is_status_regression(match.status, status):
            # Keep scores / flags; ignore regressive status.
            return match

        match.status = coalesce_forward_status(match.status, status)
        if home_score is not None:
            match.home_score = home_score
        if away_score is not None:
            match.away_score = away_score
        if match_minute is not None:
            match.match_minute = match_minute
        if extra_time is not None:
            match.extra_time = extra_time
        if penalties is not None:
            match.penalties = penalties
        if matchday is not None:
            match.matchday = matchday
        return await self._matches.save(match)

    async def list_predictions(self) -> list[EurosPrediction]:
        return await self._predictions.list_active()

    async def get_prediction_for_user(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> EurosPrediction | None:
        return await self._predictions.get_active(auth_id=auth_id, match_id=match_id)

    async def submit_prediction(
        self,
        *,
        auth_id: UUID,
        match_id: int,
        prediction: str,
        extra_time: bool | None = None,
        penalties: bool | None = None,
        now: datetime | None = None,
        enforce_cutoff: bool = True,
    ) -> EurosPrediction:
        choice = _parse_user_choice(prediction)
        et = bool(extra_time)
        pens = bool(penalties)
        if choice == PredictionChoice.DRAW and (et or pens):
            raise EurosPredictionError(
                "Cannot combine draw with extra time or penalties."
            )

        match = await self.get_match(match_id)
        if enforce_cutoff:
            cutoff_at = (now or datetime.now(UTC)) + _CUTOFF
            if cutoff_at >= _as_utc(match.match_date):
                raise EurosPredictionError("Past cutoff for prediction set.")

        plus_ninety = et or pens
        existing = await self._predictions.get_row(auth_id=auth_id, match_id=match_id)
        if existing is not None:
            existing.prediction = choice.value
            existing.plus_ninety = plus_ninety
            existing.extra_time = et
            existing.penalties = pens
            existing.dropped = False
            return await self._predictions.save(existing)

        return await self._predictions.add(
            EurosPrediction(
                auth_id=auth_id,
                match_id=match_id,
                prediction=choice.value,
                plus_ninety=plus_ninety,
                extra_time=et,
                penalties=pens,
            )
        )

    async def list_scores(self, *, bearer_token: str | None = None) -> list[EurosScoreRow]:
        """Aggregate legacy-shaped scoreboard (player name, score, bonus, total)."""
        finished = {m.id: m for m in await self._matches.list_finished() if m.id is not None}
        if not finished:
            return await self._empty_scoreboard(bearer_token)

        predictions = await self._predictions.list_active_for_matches(list(finished.keys()))
        totals: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
        for pred in predictions:
            match = finished.get(pred.match_id)
            if match is None:
                continue
            score, bonus = score_prediction(pred, match)
            bucket = totals[pred.auth_id]
            bucket[0] += score
            bucket[1] += bonus

        names = await self._resolve_player_names(
            set(totals.keys()),
            bearer_token=bearer_token,
        )
        rows = [
            EurosScoreRow(
                player=names.get(auth_id, str(auth_id)),
                score=score,
                bonus=bonus,
                total=score + bonus,
                auth_id=auth_id,
            )
            for auth_id, (score, bonus) in totals.items()
        ]
        # Include euros-role roster with zero totals (legacy left-joined EUROS_PLAYERS).
        if bearer_token and self._profiles is not None:
            for auth_id, name in (await self._euros_roster_names(bearer_token)).items():
                if auth_id not in totals:
                    rows.append(
                        EurosScoreRow(
                            player=name,
                            score=0,
                            bonus=0,
                            total=0,
                            auth_id=auth_id,
                        )
                    )
        rows.sort(key=lambda row: (-row.total, row.player.lower()))
        return rows

    async def _empty_scoreboard(self, bearer_token: str | None) -> list[EurosScoreRow]:
        if not bearer_token or self._profiles is None:
            return []
        names = await self._euros_roster_names(bearer_token)
        rows = [
            EurosScoreRow(player=name, score=0, bonus=0, total=0, auth_id=auth_id)
            for auth_id, name in names.items()
        ]
        rows.sort(key=lambda row: row.player.lower())
        return rows

    async def _euros_roster_names(self, bearer_token: str) -> dict[UUID, str]:
        assert self._profiles is not None
        try:
            roster = await self._profiles.list_profiles(bearer_token, active=True)
        except RosterError:
            return {}
        names: dict[UUID, str] = {}
        for profile in roster:
            if await self._profile_has_euros_role(profile):
                names[profile.auth_id] = _display_name(profile)
        return names

    async def _resolve_player_names(
        self,
        auth_ids: set[UUID],
        *,
        bearer_token: str | None,
    ) -> dict[UUID, str]:
        if not auth_ids or self._profiles is None or not bearer_token:
            return {}
        try:
            roster = await self._profiles.list_profiles(bearer_token, active=None)
        except RosterError:
            return {}
        return {
            profile.auth_id: _display_name(profile)
            for profile in roster
            if profile.auth_id in auth_ids
        }

    async def _profile_has_euros_role(self, profile: RosterProfile) -> bool:
        if self._auth_admin is None or not profile.email:
            return False
        try:
            membership = await self._auth_admin.get_membership(email=profile.email)
        except AuthAdminError:
            return False
        return EUROS_ROLE in {role.strip().lower() for role in membership.roles}


def _display_name(profile: RosterProfile) -> str:
    first = (profile.first_name or "").strip()
    last = (profile.last_name or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    if profile.nickname:
        return profile.nickname
    if profile.email:
        return profile.email
    return str(profile.auth_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_user_choice(value: str) -> PredictionChoice:
    normalized = value.strip().lower()
    choice = _CHOICE_ALIASES.get(normalized)
    if choice is None or choice not in _USER_CHOICES:
        raise EurosPredictionError(
            "prediction must be one of: home, away, draw (or H/D/A)."
        )
    return choice
