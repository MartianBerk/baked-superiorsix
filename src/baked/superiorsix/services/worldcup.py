"""World Cup / Qatar Hero side-game service (legacy WORLDCUP_* behaviour)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from baked.superiorsix.domain.exceptions import (
    PredictionError,
    WorldcupError,
    WorldcupMatchNotFoundError,
)
from baked.superiorsix.domain.models import PredictionChoice
from baked.superiorsix.domain.worldcup import (
    WorldcupMatch,
    WorldcupPrediction,
    WorldcupScoreRow,
    WorldcupSettings,
    score_prediction_against_match,
    static_pot_pence,
)
from baked.superiorsix.repositories.worldcup_matches import WorldcupMatchRepository
from baked.superiorsix.repositories.worldcup_predictions import WorldcupPredictionRepository
from baked.superiorsix.repositories.worldcup_settings import WorldcupSettingsRepository

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


class WorldcupService:
    def __init__(
        self,
        matches: WorldcupMatchRepository,
        predictions: WorldcupPredictionRepository,
        settings: WorldcupSettingsRepository,
    ) -> None:
        self._matches = matches
        self._predictions = predictions
        self._settings = settings

    async def list_matches(self) -> list[WorldcupMatch]:
        return await self._matches.list_all()

    async def get_match(self, match_id: int) -> WorldcupMatch:
        match = await self._matches.get(match_id)
        if match is None:
            raise WorldcupMatchNotFoundError(f"World Cup match {match_id} not found.")
        return match

    async def get_settings(self) -> WorldcupSettings:
        return await self._settings.get()

    async def update_settings(
        self,
        *,
        buy_in_pence: int | None = None,
        participant_count: int | None = None,
    ) -> WorldcupSettings:
        current = await self._settings.get()
        if buy_in_pence is not None:
            if buy_in_pence < 0:
                raise WorldcupError("buy_in_pence must be >= 0.")
            current.buy_in_pence = buy_in_pence
        if participant_count is not None:
            if participant_count < 0:
                raise WorldcupError("participant_count must be >= 0.")
            current.participant_count = participant_count
        return await self._settings.save(current)

    async def meta(self) -> dict[str, int]:
        settings = await self.get_settings()
        return {
            "buy_in_pence": settings.buy_in_pence,
            "participant_count": settings.participant_count,
            "pot_pence": static_pot_pence(
                buy_in_pence=settings.buy_in_pence,
                participant_count=settings.participant_count,
            ),
        }

    async def list_predictions(self) -> list[WorldcupPrediction]:
        return await self._predictions.list_active()

    async def get_prediction_for_user(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> WorldcupPrediction | None:
        await self.get_match(match_id)
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
    ) -> WorldcupPrediction:
        choice = _parse_user_choice(prediction)
        et = bool(extra_time)
        pens = bool(penalties)
        if choice == PredictionChoice.DRAW and (et or pens):
            raise PredictionError("Cannot combine draw with extra time or penalties.")

        match = await self.get_match(match_id)
        if enforce_cutoff:
            cutoff_at = (now or datetime.now(UTC)) + _CUTOFF
            kickoff = match.match_date
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=UTC)
            if cutoff_at >= kickoff:
                raise PredictionError("Past cutoff for prediction set.")

        plus_ninety = et or pens
        existing = await self._predictions.get_row(auth_id=auth_id, match_id=match_id)
        if existing is not None:
            existing.prediction = choice.value
            existing.extra_time = et
            existing.penalties = pens
            existing.plus_ninety = plus_ninety
            existing.dropped = False
            return await self._predictions.save(existing)

        return await self._predictions.add(
            WorldcupPrediction(
                auth_id=auth_id,
                match_id=match_id,
                prediction=choice.value,
                plus_ninety=plus_ninety,
                extra_time=et,
                penalties=pens,
            )
        )

    async def list_scores(
        self,
        *,
        display_names: dict[UUID, str] | None = None,
    ) -> list[WorldcupScoreRow]:
        """Aggregate leaderboard (legacy WORLDCUP_SCORES view rules in Python)."""
        finished = {m.id: m for m in await self._matches.list_finished() if m.id is not None}
        if not finished:
            return []

        tallies: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
        for prediction in await self._predictions.list_active():
            match = finished.get(prediction.match_id)
            if match is None:
                continue
            score, bonus = score_prediction_against_match(prediction, match)
            tallies[prediction.auth_id][0] += score
            tallies[prediction.auth_id][1] += bonus

        names = display_names or {}
        rows = [
            WorldcupScoreRow(
                auth_id=auth_id,
                player=names.get(auth_id, str(auth_id)),
                score=score,
                bonus=bonus,
                total=score + bonus,
            )
            for auth_id, (score, bonus) in tallies.items()
        ]
        rows.sort(key=lambda row: (-row.total, -row.score, str(row.auth_id)))
        return rows


def _parse_user_choice(raw: str) -> PredictionChoice:
    key = raw.strip().lower()
    choice = _CHOICE_ALIASES.get(key)
    if choice is None or choice not in _USER_CHOICES:
        raise PredictionError("prediction must be home, away, or draw.")
    return choice
