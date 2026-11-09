import logging
from collections.abc import Collection
from datetime import UTC, date, datetime, time, timedelta

from baked.superiorsix.domain.exceptions import MatchNotFoundError, MatchSelectionError
from baked.superiorsix.domain.match_status import coalesce_forward_status, is_status_regression
from baked.superiorsix.domain.models import (
    LeagueTableEntry,
    Match,
    MatchDetail,
    MatchDifficultyScore,
    MatchStatus,
    TeamResult,
)
from baked.superiorsix.repositories.matches import MatchRepository
from baked.superiorsix.services.difficulty import calculate_match_difficulty
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.team_xref import TeamXrefService

logger = logging.getLogger(__name__)


class MatchService:
    def __init__(
        self,
        matches: MatchRepository,
        leagues: LeagueService,
        team_xref: TeamXrefService | None = None,
    ) -> None:
        self._matches = matches
        self._leagues = leagues
        self._team_xref = team_xref

    async def _canonical(self, team: str) -> str:
        if self._team_xref is None:
            return team
        return await self._team_xref.canonical(team)

    async def _aliases(self, team: str) -> list[str]:
        if self._team_xref is None:
            return [team]
        return await self._team_xref.aliases(team)

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

    async def selected_matches(self) -> list[Match]:
        """All Super Six selections across days (``use_match``)."""
        return await self._matches.list_selected()

    async def selected_matches_for_subround(self, subround_id: int) -> list[Match]:
        """Super Six selections belonging to one prediction subround."""
        return await self._matches.list_selected_for_subround(subround_id)

    async def list_score_candidates(
        self,
        start: datetime,
        end: datetime,
        *,
        use_match: bool | None = None,
        statuses: frozenset[str] | None = None,
    ) -> list[Match]:
        """Fixtures in ``[start, end)`` eligible for auto-score pulls."""
        return await self._matches.list_score_candidates(
            start,
            end,
            use_match=use_match,
            statuses=statuses,
        )

    async def upsert_fixture(self, match: Match) -> Match:
        """Process ingest path: create or update a fixture by external_id.

        Preserves Super Six selection fields (``use_match`` / ``game_number`` /
        ``subround_id``) when an existing row is updated from a feed.

        Match status only moves forward along the lifecycle (see
        ``coalesce_forward_status``). Regressive feed statuses are ignored; when
        that happens, null incoming scores / minute do not wipe stored values.
        """
        existing = await self._matches.get_by_external_id(match.external_id)
        if existing is None:
            return await self._matches.add(match)

        match.id = existing.id
        match.use_match = existing.use_match
        match.game_number = existing.game_number
        match.subround_id = existing.subround_id
        # Difficulty is immutable once computed (SS-125); never overwrite from feeds.
        match.difficulty_score = existing.difficulty_score
        match.difficulty_band = existing.difficulty_band
        incoming_status = match.status
        if is_status_regression(existing.status, incoming_status):
            logger.warning(
                "rejecting regressive match status external_id=%s current=%s incoming=%s",
                match.external_id,
                existing.status,
                incoming_status,
            )
            if match.home_score is None:
                match.home_score = existing.home_score
            if match.away_score is None:
                match.away_score = existing.away_score
            if match.match_minute is None:
                match.match_minute = existing.match_minute
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
    ) -> Match:
        """Process ingest path: live or final score updates.

        Status updates are forward-only; scores / minute still apply when the
        incoming status would otherwise regress.
        """
        match = await self._resolve(external_id=external_id, match_id=match_id)
        if is_status_regression(match.status, status):
            logger.warning(
                "rejecting regressive score status match_id=%s external_id=%s "
                "current=%s incoming=%s",
                match.id,
                match.external_id,
                match.status,
                status,
            )
            status = match.status
        else:
            status = coalesce_forward_status(match.status, status)
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

    async def list_in_range(self, *, start: datetime, end: datetime) -> list[Match]:
        """Fixtures with kickoff in ``[start, end)`` (UTC day bounds from the router)."""
        matches = await self._matches.list_between(start, end)
        return sorted(
            matches,
            key=lambda match: (match.match_date, match.game_number or 0, match.id or 0),
        )

    async def select_for_round(
        self,
        match_id: int,
        game_number: int,
        *,
        subround_id: int,
    ) -> Match:
        """Mark a fixture as a Super Six selection in ``subround_id`` (slots 1–6)."""
        if game_number < 1 or game_number > 6:
            raise MatchSelectionError("Invalid game_number, must be between 1 and 6.")

        match = await self.get(match_id)
        _reject_postponed_selection(match)
        selected = await self.selected_matches_for_subround(subround_id)

        if (
            match.use_match
            and match.subround_id == subround_id
            and match.game_number != game_number
        ):
            raise MatchSelectionError(f"Match already selected as game number {match.game_number}")

        if len(selected) >= 6 and not (
            match.use_match and match.subround_id == subround_id
        ):
            raise MatchSelectionError(
                "Six matches already selected. Drop a match first."
            )

        for existing in selected:
            if existing.game_number == game_number and existing.id != match.id:
                existing.use_match = False
                existing.game_number = None
                existing.subround_id = None
                await self._matches.save(existing)

        match.use_match = True
        match.game_number = game_number
        match.subround_id = subround_id
        # Freeze difficulty at selection so later table/form changes cannot rewrite pips.
        match = await self._ensure_difficulty_persisted(match)
        return await self._matches.save(match)

    async def deselect_from_round(self, match_id: int) -> Match:
        """Clear Super Six selection flags on a fixture (legacy ``dropmatch``)."""
        match = await self.get(match_id)
        match.use_match = False
        match.game_number = None
        match.subround_id = None
        return await self._matches.save(match)

    async def replace_day_selections(
        self,
        match_date: date,
        selections: list[tuple[int, int]],
        *,
        subround_id: int,
    ) -> list[Match]:
        """Replace Super Six selections for one calendar day in a subround.

        ``selections`` is ``(match_id, game_number)`` for 0–6 fixtures on that
        day. Game numbers must be unique and in ``1..6`` (sparse OK). Fewer
        than six is allowed; slots remain unique within the subround across
        other days (assigning game N clears that slot elsewhere in the same
        subround). An empty list clears this day's selections for the
        subround only.
        """
        if len(selections) > 6:
            raise MatchSelectionError("must submit at most 6 matches")
        if len({match_id for match_id, _ in selections}) != len(selections):
            raise MatchSelectionError("duplicate match id in selections")
        game_numbers = [game_number for _, game_number in selections]
        if any(number < 1 or number > 6 for number in game_numbers):
            raise MatchSelectionError("game_numbers must be between 1 and 6")
        if len(set(game_numbers)) != len(game_numbers):
            raise MatchSelectionError("duplicate game_number in selections")

        for existing in await self.available_matches(match_date):
            if existing.subround_id not in (None, subround_id):
                # Leave selections that belong to another subround untouched.
                continue
            existing.use_match = False
            existing.game_number = None
            existing.subround_id = None
            await self._matches.save(existing)

        claimed = set(game_numbers)
        if claimed:
            for existing in await self.selected_matches_for_subround(subround_id):
                day = existing.match_date.astimezone(UTC).date()
                if day == match_date:
                    continue
                if existing.game_number in claimed:
                    existing.use_match = False
                    existing.game_number = None
                    existing.subround_id = None
                    await self._matches.save(existing)

        updated: list[Match] = []
        for match_id, game_number in selections:
            match = await self.get(match_id)
            _reject_postponed_selection(match)
            day = match.match_date.astimezone(UTC).date()
            if day != match_date:
                raise MatchSelectionError(
                    f"Match {match_id} kickoff is not on {match_date}."
                )
            match.use_match = True
            match.game_number = game_number
            match.subround_id = subround_id
            match = await self._ensure_difficulty_persisted(match)
            updated.append(await self._matches.save(match))
        return sorted(updated, key=lambda match: match.game_number or 0)

    async def display_team(self, team: str) -> str:
        """API-facing team label (xref when mapped)."""
        if self._team_xref is None:
            return team
        return await self._team_xref.display(team)

    async def team_performance(self, team: str, before: datetime) -> list[TeamResult]:
        aliases = await self._aliases(team)
        matches = await self._matches.list_finished_for_team_before(aliases, before)
        return [_result_for_team(match, aliases) for match in matches[-5:]]

    async def head_to_head(
        self,
        home_team: str,
        away_team: str,
        before: datetime,
    ) -> tuple[list[TeamResult], list[TeamResult]]:
        home_aliases = await self._aliases(home_team)
        away_aliases = await self._aliases(away_team)
        matches = await self._matches.list_finished_h2h_before(
            home_aliases,
            away_aliases,
            before,
        )
        home_results: list[TeamResult] = []
        away_results: list[TeamResult] = []
        for match in matches[-5:]:
            home_results.append(_result_for_team(match, home_aliases))
            away_results.append(_result_for_team(match, away_aliases))
        return home_results, away_results

    async def match_detail(
        self,
        home_team: str,
        away_team: str,
        match_date: date,
    ) -> MatchDetail:
        start, end = day_bounds(match_date)
        home_aliases = await self._aliases(home_team)
        away_aliases = await self._aliases(away_team)
        match = await self._matches.find_fixture(home_aliases, away_aliases, start, end)
        if match is None:
            raise MatchNotFoundError("Cannot find match.")

        league = await self._leagues.get(match.league_id)
        table = await self._leagues.league_table(league.code)

        # Response keys are xref display names so the FE never remaps feeds.
        home_label = await self.display_team(match.home_team)
        away_label = await self.display_team(match.away_team)

        positions = {entry.team: entry.position for entry in table}
        before = start
        home_h2h, away_h2h = await self.head_to_head(match.home_team, match.away_team, before)
        home_form = await self.team_performance(match.home_team, before)
        away_form = await self.team_performance(match.away_team, before)
        home_canonical = await self._canonical(match.home_team)
        away_canonical = await self._canonical(match.away_team)
        home_position = positions.get(home_canonical)
        away_position = positions.get(away_canonical)
        difficulty = await self.difficulty_for_match(match)

        return MatchDetail(
            league_position={
                home_label: home_position,
                away_label: away_position,
            },
            team_performance={
                home_label: home_form,
                away_label: away_form,
            },
            head_to_head={
                home_label: home_h2h,
                away_label: away_h2h,
            },
            difficulty=difficulty,
        )

    async def difficulty_for_match(self, match: Match) -> MatchDifficultyScore:
        """Return frozen difficulty for a fixture, computing and persisting once."""
        frozen = _frozen_difficulty(match)
        if frozen is not None:
            return frozen
        computed = await self._compute_difficulty(match)
        await self._persist_difficulty(match, computed)
        return computed

    async def difficulties_for_matches(
        self,
        matches: list[Match],
    ) -> dict[int, MatchDifficultyScore]:
        """Batch difficulty for admin list payloads (caches league tables).

        Persists on first computation; later calls return the frozen values.
        """
        if not matches:
            return {}

        tables_by_league: dict[int, list[LeagueTableEntry]] = {}
        out: dict[int, MatchDifficultyScore] = {}
        for match in matches:
            if match.id is None:
                continue
            frozen = _frozen_difficulty(match)
            if frozen is not None:
                out[match.id] = frozen
                continue
            if match.league_id not in tables_by_league:
                league = await self._leagues.get(match.league_id)
                tables_by_league[match.league_id] = await self._leagues.league_table(league.code)
            table = tables_by_league[match.league_id]
            difficulty = await self._compute_difficulty(match, table=table)
            await self._persist_difficulty(match, difficulty)
            out[match.id] = difficulty
        return out

    async def _ensure_difficulty_persisted(self, match: Match) -> Match:
        """Compute and attach difficulty when missing; leave an existing freeze alone."""
        if _frozen_difficulty(match) is not None:
            return match
        computed = await self._compute_difficulty(match)
        match.difficulty_score = computed.score
        match.difficulty_band = computed.band
        return match

    async def _persist_difficulty(
        self,
        match: Match,
        difficulty: MatchDifficultyScore,
    ) -> Match:
        """Write difficulty onto the match row if it is not already frozen."""
        if _frozen_difficulty(match) is not None:
            return match
        match.difficulty_score = difficulty.score
        match.difficulty_band = difficulty.band
        if match.id is None:
            return match
        return await self._matches.save(match)

    async def _compute_difficulty(
        self,
        match: Match,
        *,
        table: list[LeagueTableEntry] | None = None,
    ) -> MatchDifficultyScore:
        """Score difficulty from pre-kickoff form/H2H and the current league table.

        Form and H2H are already capped to matches before the fixture's calendar
        day. Callers must persist the result so later table updates cannot
        rewrite the band.
        """
        if table is None:
            league = await self._leagues.get(match.league_id)
            table = await self._leagues.league_table(league.code)
        positions = {entry.team: entry.position for entry in table}
        before = day_bounds(match.match_date.astimezone(UTC).date())[0]
        home_h2h, away_h2h = await self.head_to_head(match.home_team, match.away_team, before)
        home_form = await self.team_performance(match.home_team, before)
        away_form = await self.team_performance(match.away_team, before)
        home_key = await self._canonical(match.home_team)
        away_key = await self._canonical(match.away_team)
        difficulty = calculate_match_difficulty(
            home_performance=home_form,
            away_performance=away_form,
            home_position=positions.get(home_key),
            away_position=positions.get(away_key),
            home_head_to_head=home_h2h,
            away_head_to_head=away_h2h,
            league_size=len(table) or None,
        )
        return MatchDifficultyScore(score=difficulty.score, band=difficulty.band)

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


def _reject_postponed_selection(match: Match) -> None:
    """Block postponed fixtures from Super Six slate selection."""
    if match.status == MatchStatus.POSTPONED:
        match_id = match.id if match.id is not None else "?"
        raise MatchSelectionError(
            f"Match {match_id} is postponed and cannot be selected."
        )


def _frozen_difficulty(match: Match) -> MatchDifficultyScore | None:
    """Return persisted difficulty when both score and band are set."""
    if match.difficulty_score is None or match.difficulty_band is None:
        return None
    return MatchDifficultyScore(score=match.difficulty_score, band=match.difficulty_band)


def day_bounds(match_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(match_date, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _result_for_team(match: Match, team_names: str | Collection[str]) -> TeamResult:
    names = {team_names} if isinstance(team_names, str) else set(team_names)
    if match.home_score is None or match.away_score is None:
        return TeamResult.DRAW
    if match.home_team in names:
        if match.home_score > match.away_score:
            return TeamResult.WIN
        if match.home_score < match.away_score:
            return TeamResult.LOSE
        return TeamResult.DRAW
    if match.away_team in names:
        if match.away_score > match.home_score:
            return TeamResult.WIN
        if match.away_score < match.home_score:
            return TeamResult.LOSE
        return TeamResult.DRAW
    return TeamResult.DRAW
