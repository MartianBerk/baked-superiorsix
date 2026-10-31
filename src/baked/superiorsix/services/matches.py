from datetime import UTC, date, datetime, time, timedelta

from baked.superiorsix.domain.exceptions import MatchNotFoundError, MatchSelectionError
from baked.superiorsix.domain.models import Match, MatchDetail, MatchStatus, TeamResult
from baked.superiorsix.repositories.matches import MatchRepository
from baked.superiorsix.services.leagues import LeagueService


class MatchService:
    def __init__(self, matches: MatchRepository, leagues: LeagueService) -> None:
        self._matches = matches
        self._leagues = leagues

    async def get(self, match_id: int) -> Match:
        match = await self._matches.get(match_id)
        if match is None:
            raise MatchNotFoundError(f"Match {match_id} not found.")
        return match

    async def list_on_date(self, match_date: date, *, available_only: bool = False) -> list[Match]:
        start, end = day_bounds(match_date)
        return await self._matches.list_between(
            start,
            end,
            use_match=True if available_only else None,
        )

    async def available_matches(self, match_date: date) -> list[Match]:
        matches = await self.list_on_date(match_date, available_only=True)
        return sorted(matches, key=lambda match: match.game_number or 0)

    async def upsert_fixture(self, match: Match) -> Match:
        """Process ingest path: create or update a fixture by external_id.

        Preserves Super Six selection fields (``use_match`` / ``game_number``)
        when an existing row is updated from a feed.
        """
        existing = await self._matches.get_by_external_id(match.external_id)
        if existing is None:
            return await self._matches.add(match)

        match.id = existing.id
        match.use_match = existing.use_match
        match.game_number = existing.game_number
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
    ) -> Match:
        """Process ingest path: live or final score updates."""
        match = await self._resolve(external_id=external_id, match_id=match_id)
        match.status = status
        if home_score is not None:
            match.home_score = home_score
        if away_score is not None:
            match.away_score = away_score
        if match_minute is not None:
            match.match_minute = match_minute
        elif status == MatchStatus.FINISHED and match.match_minute is None:
            match.match_minute = 90
        return await self._matches.save(match)

    async def select_for_round(self, match_id: int, game_number: int) -> Match:
        """Mark a fixture as one of the day's Super Six selections."""
        if game_number < 1 or game_number > 6:
            raise MatchSelectionError("Invalid game_number, must be between 1 and 6.")

        match = await self.get(match_id)
        day = match.match_date.astimezone(UTC).date()
        selected = await self.available_matches(day)

        if match.use_match and match.game_number != game_number:
            raise MatchSelectionError(f"Match already selected as game number {match.game_number}")

        if len(selected) >= 6 and not match.use_match:
            raise MatchSelectionError(
                f"Six matches already submitted for {day}. Drop a match first."
            )

        for existing in selected:
            if existing.game_number == game_number and existing.id != match.id:
                existing.use_match = False
                existing.game_number = None
                await self._matches.save(existing)

        match.use_match = True
        match.game_number = game_number
        return await self._matches.save(match)

    async def team_performance(self, team: str, before: datetime) -> list[TeamResult]:
        matches = await self._matches.list_finished_for_team_before(team, before)
        return [_result_for_team(match, team) for match in matches[-5:]]

    async def head_to_head(
        self,
        home_team: str,
        away_team: str,
        before: datetime,
    ) -> tuple[list[TeamResult], list[TeamResult]]:
        matches = await self._matches.list_finished_h2h_before(home_team, away_team, before)
        home_results: list[TeamResult] = []
        away_results: list[TeamResult] = []
        for match in matches[-5:]:
            home_results.append(_result_for_team(match, home_team))
            away_results.append(_result_for_team(match, away_team))
        return home_results, away_results

    async def match_detail(
        self,
        home_team: str,
        away_team: str,
        match_date: date,
    ) -> MatchDetail:
        start, end = day_bounds(match_date)
        match = await self._matches.find_fixture(home_team, away_team, start, end)
        if match is None:
            raise MatchNotFoundError("Cannot find match.")

        league = await self._leagues.get(match.league_id)
        table = await self._leagues.league_table(league.code)

        positions = {entry.team: entry.position for entry in table}
        before = start
        home_h2h, away_h2h = await self.head_to_head(home_team, away_team, before)

        return MatchDetail(
            league_position={
                home_team: positions.get(home_team),
                away_team: positions.get(away_team),
            },
            team_performance={
                home_team: await self.team_performance(home_team, before),
                away_team: await self.team_performance(away_team, before),
            },
            head_to_head={
                home_team: home_h2h,
                away_team: away_h2h,
            },
        )

    async def _resolve(
        self,
        *,
        external_id: str | None,
        match_id: int | None,
    ) -> Match:
        if match_id is not None:
            return await self.get(match_id)
        if external_id is not None:
            match = await self._matches.get_by_external_id(external_id)
            if match is None:
                raise MatchNotFoundError(f"Match '{external_id}' not found.")
            return match
        raise MatchNotFoundError("Match id or external_id is required.")


def day_bounds(match_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(match_date, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _result_for_team(match: Match, team: str) -> TeamResult:
    if match.home_score is None or match.away_score is None:
        return TeamResult.DRAW
    if match.home_team == team:
        if match.home_score > match.away_score:
            return TeamResult.WIN
        if match.home_score < match.away_score:
            return TeamResult.LOSE
        return TeamResult.DRAW
    if match.away_team == team:
        if match.away_score > match.home_score:
            return TeamResult.WIN
        if match.away_score < match.home_score:
            return TeamResult.LOSE
        return TeamResult.DRAW
    return TeamResult.DRAW
