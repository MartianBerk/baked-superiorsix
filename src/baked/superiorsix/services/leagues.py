from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date

from baked.superiorsix.domain.exceptions import LeagueNotFoundError
from baked.superiorsix.domain.models import League, LeagueTableEntry, Match, MatchStatus
from baked.superiorsix.repositories.leagues import LeagueRepository
from baked.superiorsix.repositories.matches import MatchRepository


def season_label(start_date: date) -> str:
    """Legacy-style season string from a league start date, e.g. 2025/26."""
    year = start_date.year
    return f"{year}/{str(year + 1)[-2:]}"


def season_for_match_date(match_date: date) -> str:
    """Aug–Jul season for a match date (month < 8 belongs to previous start year)."""
    if match_date.month < 8:
        start_year = match_date.year - 1
    else:
        start_year = match_date.year
    return f"{start_year}/{str(start_year + 1)[-2:]}"


class LeagueService:
    def __init__(self, leagues: LeagueRepository, matches: MatchRepository) -> None:
        self._leagues = leagues
        self._matches = matches

    async def get(self, league_id: int) -> League:
        league = await self._leagues.get(league_id)
        if league is None:
            raise LeagueNotFoundError(f"League {league_id} not found.")
        return league

    async def get_by_code(self, code: str) -> League:
        league = await self._leagues.get_by_code(code)
        if league is None:
            raise LeagueNotFoundError(f"League '{code}' not found.")
        return league

    async def list(self) -> list[League]:
        return await self._leagues.list()

    async def upsert(self, league: League) -> League:
        """Process ingest path for league metadata (football-data style feed)."""
        existing = await self._leagues.get_by_code(league.code)
        if existing is None:
            return await self._leagues.add(league)
        league.id = existing.id
        return await self._leagues.save(league)

    async def league_table(
        self,
        league_code: str,
        season: str | None = None,
    ) -> list[LeagueTableEntry]:
        league = await self.get_by_code(league_code)
        if league.id is None:
            raise LeagueNotFoundError(f"League '{league_code}' not found.")
        season = season or season_label(league.start_date)
        matches = await self._matches.list_by_league(league.id)
        return build_league_table(league_code, season, matches)


def build_league_table(
    league_code: str,
    season: str,
    matches: list[Match],
) -> list[LeagueTableEntry]:
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "goals_for": 0,
            "goals_against": 0,
            "points": 0,
        }
    )

    for match in matches:
        if season_for_match_date(match.match_date.date()) != season:
            continue
        if match.status != MatchStatus.FINISHED:
            continue
        if match.home_score is None or match.away_score is None:
            continue

        home = stats[match.home_team]
        away = stats[match.away_team]
        home["played"] += 1
        away["played"] += 1
        home["goals_for"] += match.home_score
        home["goals_against"] += match.away_score
        away["goals_for"] += match.away_score
        away["goals_against"] += match.home_score

        if match.home_score > match.away_score:
            home["won"] += 1
            home["points"] += 3
            away["lost"] += 1
        elif match.away_score > match.home_score:
            away["won"] += 1
            away["points"] += 3
            home["lost"] += 1
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["points"] += 1
            away["points"] += 1

    entries = [
        LeagueTableEntry(
            league=league_code,
            season=season,
            team=team,
            matches_played=values["played"],
            matches_won=values["won"],
            matches_drawn=values["drawn"],
            matches_lost=values["lost"],
            goals_for=values["goals_for"],
            goals_against=values["goals_against"],
            goal_difference=values["goals_for"] - values["goals_against"],
            points=values["points"],
        )
        for team, values in stats.items()
    ]
    entries.sort(
        key=lambda entry: (entry.points, entry.goal_difference, entry.goals_for),
        reverse=True,
    )
    return _assign_positions(entries)


def _assign_positions(entries: list[LeagueTableEntry]) -> list[LeagueTableEntry]:
    positioned: list[LeagueTableEntry] = []
    for index, entry in enumerate(entries):
        if (
            index > 0
            and entries[index - 1].points == entry.points
            and entries[index - 1].goal_difference == entry.goal_difference
            and entries[index - 1].goals_for == entry.goals_for
        ):
            previous = positioned[-1]
            tied = (
                previous.position if previous.position.startswith("T") else f"T{previous.position}"
            )
            positioned[-1] = replace(previous, position=tied)
            positioned.append(replace(entry, position=tied))
        else:
            positioned.append(replace(entry, position=str(index + 1)))
    return positioned
