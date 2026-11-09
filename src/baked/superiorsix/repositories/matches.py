from collections.abc import Collection
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import MatchRecord
from baked.superiorsix.domain.models import Match, MatchStatus


def _stamp_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize DB datetimes to aware UTC (SQLite may drop tzinfo)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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

    async def list_selected(self) -> list[Match]: ...

    async def list_selected_for_subround(self, subround_id: int) -> list[Match]: ...

    async def list_score_candidates(
        self,
        start: datetime,
        end: datetime,
        *,
        use_match: bool | None = None,
        statuses: frozenset[str] | None = None,
    ) -> list[Match]: ...

    async def list_by_league(self, league_id: int) -> list[Match]: ...

    async def list_finished_for_team_before(
        self,
        teams: Collection[str],
        before: datetime,
    ) -> list[Match]: ...

    async def list_finished_h2h_before(
        self,
        home_teams: Collection[str],
        away_teams: Collection[str],
        before: datetime,
    ) -> list[Match]: ...

    async def find_fixture(
        self,
        home_teams: Collection[str],
        away_teams: Collection[str],
        start: datetime,
        end: datetime,
        *,
        prefer_home: str | None = None,
        prefer_away: str | None = None,
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

    async def list_selected(self) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(MatchRecord.use_match.is_(True))
            .order_by(MatchRecord.game_number.nulls_last(), MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_selected_for_subround(self, subround_id: int) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.use_match.is_(True),
                MatchRecord.subround_id == subround_id,
            )
            .order_by(MatchRecord.game_number.nulls_last(), MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_score_candidates(
        self,
        start: datetime,
        end: datetime,
        *,
        use_match: bool | None = None,
        statuses: frozenset[str] | None = None,
    ) -> list[Match]:
        clauses = [
            MatchRecord.match_date >= start,
            MatchRecord.match_date < end,
        ]
        if use_match is not None:
            clauses.append(MatchRecord.use_match.is_(use_match))
        if statuses is not None:
            clauses.append(MatchRecord.status.in_(tuple(statuses)))
        result = await self._session.execute(
            select(MatchRecord)
            .where(and_(*clauses))
            .order_by(MatchRecord.match_date, MatchRecord.id)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_by_league(self, league_id: int) -> list[Match]:
        result = await self._session.execute(
            select(MatchRecord)
            .where(MatchRecord.league_id == league_id)
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_finished_for_team_before(
        self,
        teams: Collection[str],
        before: datetime,
    ) -> list[Match]:
        names = tuple(dict.fromkeys(teams))
        if not names:
            return []
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.status == MatchStatus.FINISHED,
                MatchRecord.match_date < before,
                or_(MatchRecord.home_team.in_(names), MatchRecord.away_team.in_(names)),
            )
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_finished_h2h_before(
        self,
        home_teams: Collection[str],
        away_teams: Collection[str],
        before: datetime,
    ) -> list[Match]:
        homes = tuple(dict.fromkeys(home_teams))
        aways = tuple(dict.fromkeys(away_teams))
        if not homes or not aways:
            return []
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.status == MatchStatus.FINISHED,
                MatchRecord.match_date < before,
                or_(
                    and_(
                        MatchRecord.home_team.in_(homes),
                        MatchRecord.away_team.in_(aways),
                    ),
                    and_(
                        MatchRecord.home_team.in_(aways),
                        MatchRecord.away_team.in_(homes),
                    ),
                ),
            )
            .order_by(MatchRecord.match_date)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def find_fixture(
        self,
        home_teams: Collection[str],
        away_teams: Collection[str],
        start: datetime,
        end: datetime,
        *,
        prefer_home: str | None = None,
        prefer_away: str | None = None,
    ) -> Match | None:
        """Return one fixture for the home/away name sets in ``[start, end)``.

        Alias expansion can match several rows on the same day (duplicate
        provider spellings). Prefer an exact ``prefer_home``/``prefer_away``
        hit, then a single ``use_match`` selection, then earliest kickoff/id.
        Never raises ``MultipleResultsFound``.
        """
        homes = tuple(dict.fromkeys(home_teams))
        aways = tuple(dict.fromkeys(away_teams))
        if not homes or not aways:
            return None
        result = await self._session.execute(
            select(MatchRecord)
            .where(
                MatchRecord.home_team.in_(homes),
                MatchRecord.away_team.in_(aways),
                MatchRecord.match_date >= start,
                MatchRecord.match_date < end,
            )
            .order_by(MatchRecord.match_date, MatchRecord.id)
        )
        records = list(result.scalars().all())
        if not records:
            return None
        return _to_domain(
            _pick_fixture_record(
                records,
                prefer_home=prefer_home,
                prefer_away=prefer_away,
            )
        )

    async def add(self, match: Match) -> Match:
        now = _stamp_now()
        record = MatchRecord(
            external_id=match.external_id,
            league_id=match.league_id,
            match_date=match.match_date,
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            game_number=match.game_number,
            subround_id=match.subround_id,
            difficulty_score=match.difficulty_score,
            difficulty_band=match.difficulty_band,
            last_updated=now,
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
        record.match_date = match.match_date
        record.match_minute = match.match_minute
        record.status = match.status
        record.home_team = match.home_team
        record.away_team = match.away_team
        record.use_match = match.use_match
        record.home_score = match.home_score
        record.away_score = match.away_score
        record.game_number = match.game_number
        record.subround_id = match.subround_id
        record.difficulty_score = match.difficulty_score
        record.difficulty_band = match.difficulty_band
        record.last_updated = _stamp_now()
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _pick_fixture_record(
    records: list[MatchRecord],
    *,
    prefer_home: str | None,
    prefer_away: str | None,
) -> MatchRecord:
    """Disambiguate alias/day collisions without an arbitrary silent pick."""
    if len(records) == 1:
        return records[0]
    candidates = records
    if prefer_home is not None and prefer_away is not None:
        exact = [
            record
            for record in candidates
            if record.home_team == prefer_home and record.away_team == prefer_away
        ]
        if len(exact) == 1:
            return exact[0]
        if exact:
            candidates = exact
    selected = [record for record in candidates if record.use_match]
    if len(selected) == 1:
        return selected[0]
    if selected:
        candidates = selected
    return candidates[0]


def _to_domain(record: MatchRecord) -> Match:
    return Match(
        id=record.id,
        external_id=record.external_id,
        league_id=record.league_id,
        match_date=_as_utc(record.match_date),
        match_minute=record.match_minute,
        status=record.status,
        home_team=record.home_team,
        away_team=record.away_team,
        use_match=record.use_match,
        home_score=record.home_score,
        away_score=record.away_score,
        game_number=record.game_number,
        last_updated=_as_utc(record.last_updated),
        subround_id=record.subround_id,
        difficulty_score=record.difficulty_score,
        difficulty_band=record.difficulty_band,
    )
