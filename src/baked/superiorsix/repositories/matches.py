from datetime import datetime
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import MatchRecord
from baked.superiorsix.domain.models import Match, MatchStatus


class MatchRepository(Protocol):
    async def get(self, match_id: int) -> Match | None: ...

    async def get_by_external_id(self, external_id: str) -> Match | None: ...

    async def list_between(
        self,
        start: datetime,
        end: datetime,
        *,
        use_match: bool | None = None,
    ) -> list[Match]: ...

    async def list_by_league(self, league_id: int) -> list[Match]: ...

    async def list_finished_for_team_before(self, team: str, before: datetime) -> list[Match]: ...

    async def list_finished_h2h_before(
        self,
        home_team: str,
        away_team: str,
        before: datetime,
    ) -> list[Match]: ...

    async def find_fixture(
        self,
        home_team: str,
        away_team: str,
        start: datetime,
        end: datetime,
    ) -> Match | None: ...

    async def add(self, match: Match) -> Match: ...

    async def save(self, match: Match) -> Match: ...


class SqlAlchemyMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, match_id: int) -> Match | None:
        record = await self._session.get(MatchRecord, match_id)
        return _to_domain(record) if record else None

    async def get_by_external_id(self, external_id: str) -> Match | None:
        result = await self._session.execute(
            select(MatchRecord).where(MatchRecord.external_id == external_id)
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def list_between(
        self,
        start: datetime,
        end: datetime,
        *,
        use_match: bool | None = None,
    ) -> list[Match]:
        clauses = [
            MatchRecord.match_date >= start,
            MatchRecord.match_date < end,
        ]
        if use_match is not None:
            clauses.append(MatchRecord.use_match.is_(use_match))
        result = await self._session.execute(
            select(MatchRecord)
            .where(and_(*clauses))
            .order_by(MatchRecord.game_number.nulls_last(), MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_by_league(self, league_id: int) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(MatchRecord.league_id == league_id)
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_finished_for_team_before(self, team: str, before: datetime) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.status == MatchStatus.FINISHED,
                MatchRecord.match_date < before,
                or_(MatchRecord.home_team == team, MatchRecord.away_team == team),
            )
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_finished_h2h_before(
        self,
        home_team: str,
        away_team: str,
        before: datetime,
    ) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.status == MatchStatus.FINISHED,
                MatchRecord.match_date < before,
                or_(
                    and_(
                        MatchRecord.home_team == home_team,
                        MatchRecord.away_team == away_team,
                    ),
                    and_(
                        MatchRecord.home_team == away_team,
                        MatchRecord.away_team == home_team,
                    ),
                ),
            )
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def find_fixture(
        self,
        home_team: str,
        away_team: str,
        start: datetime,
        end: datetime,
    ) -> Match | None:
        result = await self._session.execute(
            select(MatchRecord).where(
                MatchRecord.home_team == home_team,
                MatchRecord.away_team == away_team,
                MatchRecord.match_date >= start,
                MatchRecord.match_date < end,
            )
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def add(self, match: Match) -> Match:
        record = MatchRecord(
            external_id=match.external_id,
            league_id=match.league_id,
            matchday=match.matchday,
            match_date=match.match_date,
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            game_number=match.game_number,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, match: Match) -> Match:
        if match.id is None:
            return await self.add(match)
        record = await self._session.get(MatchRecord, match.id)
        if record is None:
            return await self.add(match)
        record.external_id = match.external_id
        record.league_id = match.league_id
        record.matchday = match.matchday
        record.match_date = match.match_date
        record.match_minute = match.match_minute
        record.status = match.status
        record.home_team = match.home_team
        record.away_team = match.away_team
        record.use_match = match.use_match
        record.home_score = match.home_score
        record.away_score = match.away_score
        record.game_number = match.game_number
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: MatchRecord) -> Match:
    return Match(
        id=record.id,
        external_id=record.external_id,
        league_id=record.league_id,
        matchday=record.matchday,
        match_date=record.match_date,
        match_minute=record.match_minute,
        status=record.status,
        home_team=record.home_team,
        away_team=record.away_team,
        use_match=record.use_match,
        home_score=record.home_score,
        away_score=record.away_score,
        game_number=record.game_number,
    )
