"""Persistence for Euro Wizard tables (isolated from Super Six / World Cup)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import (
    EurosMatchRecord,
    EurosPredictionRecord,
    EurosTournamentRecord,
)
from baked.superiorsix.domain.models import EurosMatch, EurosPrediction, EurosTournament


def _stamp_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EurosTournamentRepository(Protocol):
    async def get(self, tournament_id: int) -> EurosTournament | None: ...

    async def current(self) -> EurosTournament | None: ...

    async def list_all(self) -> list[EurosTournament]: ...

    async def add(self, tournament: EurosTournament) -> EurosTournament: ...

    async def save(self, tournament: EurosTournament) -> EurosTournament: ...


class EurosMatchRepository(Protocol):
    async def get(self, match_id: int) -> EurosMatch | None: ...

    async def get_by_external_id(self, external_id: str) -> EurosMatch | None: ...

    async def list_all(self) -> list[EurosMatch]: ...

    async def list_finished(self) -> list[EurosMatch]: ...

    async def add(self, match: EurosMatch) -> EurosMatch: ...

    async def save(self, match: EurosMatch) -> EurosMatch: ...


class EurosPredictionRepository(Protocol):
    async def get(self, prediction_id: int) -> EurosPrediction | None: ...

    async def get_active(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> EurosPrediction | None: ...

    async def get_row(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> EurosPrediction | None: ...

    async def list_active(self) -> list[EurosPrediction]: ...

    async def list_active_for_matches(self, match_ids: list[int]) -> list[EurosPrediction]: ...

    async def add(self, prediction: EurosPrediction) -> EurosPrediction: ...

    async def save(self, prediction: EurosPrediction) -> EurosPrediction: ...


class SqlAlchemyEurosTournamentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tournament_id: int) -> EurosTournament | None:
        record = await self._session.get(EurosTournamentRecord, tournament_id)
        return _tournament_to_domain(record) if record else None

    async def current(self) -> EurosTournament | None:
        result = await self._session.execute(
            select(EurosTournamentRecord)
            .where(EurosTournamentRecord.end_date.is_(None))
            .order_by(EurosTournamentRecord.id.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        return _tournament_to_domain(record) if record else None

    async def list_all(self) -> list[EurosTournament]:
        result = await self._session.execute(
            select(EurosTournamentRecord).order_by(EurosTournamentRecord.id.asc())
        )
        return [_tournament_to_domain(row) for row in result.scalars().all()]

    async def add(self, tournament: EurosTournament) -> EurosTournament:
        record = EurosTournamentRecord(
            competition_code=tournament.competition_code,
            label=tournament.label,
            start_date=tournament.start_date,
            end_date=tournament.end_date,
            buy_in_pence=tournament.buy_in_pence,
            entrant_count=tournament.entrant_count,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _tournament_to_domain(record)

    async def save(self, tournament: EurosTournament) -> EurosTournament:
        assert tournament.id is not None
        record = await self._session.get(EurosTournamentRecord, tournament.id)
        assert record is not None
        record.competition_code = tournament.competition_code
        record.label = tournament.label
        record.start_date = tournament.start_date
        record.end_date = tournament.end_date
        record.buy_in_pence = tournament.buy_in_pence
        record.entrant_count = tournament.entrant_count
        await self._session.commit()
        await self._session.refresh(record)
        return _tournament_to_domain(record)


class SqlAlchemyEurosMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, match_id: int) -> EurosMatch | None:
        record = await self._session.get(EurosMatchRecord, match_id)
        return _match_to_domain(record) if record else None

    async def get_by_external_id(self, external_id: str) -> EurosMatch | None:
        result = await self._session.execute(
            select(EurosMatchRecord).where(EurosMatchRecord.external_id == external_id)
        )
        record = result.scalar_one_or_none()
        return _match_to_domain(record) if record else None

    async def list_all(self) -> list[EurosMatch]:
        result = await self._session.execute(
            select(EurosMatchRecord).order_by(
                EurosMatchRecord.match_date.asc(),
                EurosMatchRecord.id.asc(),
            )
        )
        return [_match_to_domain(row) for row in result.scalars().all()]

    async def list_finished(self) -> list[EurosMatch]:
        result = await self._session.execute(
            select(EurosMatchRecord).where(EurosMatchRecord.status == "FINISHED")
        )
        return [_match_to_domain(row) for row in result.scalars().all()]

    async def add(self, match: EurosMatch) -> EurosMatch:
        record = EurosMatchRecord(
            external_id=match.external_id,
            league_id=match.league_id,
            matchday=match.matchday,
            match_date=_as_utc(match.match_date),
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            extra_time=match.extra_time,
            penalties=match.penalties,
            last_updated=_stamp_now(),
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _match_to_domain(record)

    async def save(self, match: EurosMatch) -> EurosMatch:
        assert match.id is not None
        record = await self._session.get(EurosMatchRecord, match.id)
        assert record is not None
        record.external_id = match.external_id
        record.league_id = match.league_id
        record.matchday = match.matchday
        record.match_date = _as_utc(match.match_date)
        record.match_minute = match.match_minute
        record.status = match.status
        record.home_team = match.home_team
        record.away_team = match.away_team
        record.use_match = match.use_match
        record.home_score = match.home_score
        record.away_score = match.away_score
        record.extra_time = match.extra_time
        record.penalties = match.penalties
        record.last_updated = _stamp_now()
        await self._session.commit()
        await self._session.refresh(record)
        return _match_to_domain(record)


class SqlAlchemyEurosPredictionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, prediction_id: int) -> EurosPrediction | None:
        record = await self._session.get(EurosPredictionRecord, prediction_id)
        return _prediction_to_domain(record) if record else None

    async def get_active(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> EurosPrediction | None:
        result = await self._session.execute(
            select(EurosPredictionRecord).where(
                EurosPredictionRecord.auth_id == auth_id,
                EurosPredictionRecord.match_id == match_id,
                EurosPredictionRecord.dropped.is_(False),
            )
        )
        record = result.scalar_one_or_none()
        return _prediction_to_domain(record) if record else None

    async def get_row(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> EurosPrediction | None:
        result = await self._session.execute(
            select(EurosPredictionRecord).where(
                EurosPredictionRecord.auth_id == auth_id,
                EurosPredictionRecord.match_id == match_id,
            )
        )
        record = result.scalar_one_or_none()
        return _prediction_to_domain(record) if record else None

    async def list_active(self) -> list[EurosPrediction]:
        result = await self._session.execute(
            select(EurosPredictionRecord).where(EurosPredictionRecord.dropped.is_(False))
        )
        return [_prediction_to_domain(row) for row in result.scalars().all()]

    async def list_active_for_matches(self, match_ids: list[int]) -> list[EurosPrediction]:
        if not match_ids:
            return []
        result = await self._session.execute(
            select(EurosPredictionRecord).where(
                EurosPredictionRecord.match_id.in_(match_ids),
                EurosPredictionRecord.dropped.is_(False),
            )
        )
        return [_prediction_to_domain(row) for row in result.scalars().all()]

    async def add(self, prediction: EurosPrediction) -> EurosPrediction:
        record = EurosPredictionRecord(
            auth_id=prediction.auth_id,
            match_id=prediction.match_id,
            prediction=prediction.prediction,
            plus_ninety=prediction.plus_ninety,
            extra_time=prediction.extra_time,
            penalties=prediction.penalties,
            dropped=prediction.dropped,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _prediction_to_domain(record)

    async def save(self, prediction: EurosPrediction) -> EurosPrediction:
        assert prediction.id is not None
        record = await self._session.get(EurosPredictionRecord, prediction.id)
        assert record is not None
        record.auth_id = prediction.auth_id
        record.match_id = prediction.match_id
        record.prediction = prediction.prediction
        record.plus_ninety = prediction.plus_ninety
        record.extra_time = prediction.extra_time
        record.penalties = prediction.penalties
        record.dropped = prediction.dropped
        await self._session.commit()
        await self._session.refresh(record)
        return _prediction_to_domain(record)


def _tournament_to_domain(record: EurosTournamentRecord) -> EurosTournament:
    return EurosTournament(
        id=record.id,
        competition_code=record.competition_code,
        label=record.label,
        start_date=record.start_date,
        end_date=record.end_date,
        buy_in_pence=record.buy_in_pence,
        entrant_count=record.entrant_count,
        created_at=_as_utc(record.created_at) if record.created_at else None,
    )


def _match_to_domain(record: EurosMatchRecord) -> EurosMatch:
    return EurosMatch(
        id=record.id,
        external_id=record.external_id,
        league_id=record.league_id,
        matchday=record.matchday,
        match_date=_as_utc(record.match_date),
        match_minute=record.match_minute,
        status=record.status,
        home_team=record.home_team,
        away_team=record.away_team,
        use_match=record.use_match,
        home_score=record.home_score,
        away_score=record.away_score,
        extra_time=bool(record.extra_time),
        penalties=bool(record.penalties),
        last_updated=_as_utc(record.last_updated) if record.last_updated else None,
    )


def _prediction_to_domain(record: EurosPredictionRecord) -> EurosPrediction:
    return EurosPrediction(
        id=record.id,
        auth_id=record.auth_id,
        match_id=record.match_id,
        prediction=record.prediction,
        plus_ninety=bool(record.plus_ninety),
        extra_time=bool(record.extra_time),
        penalties=bool(record.penalties),
        dropped=bool(record.dropped),
    )
