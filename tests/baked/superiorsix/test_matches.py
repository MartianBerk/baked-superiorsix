from datetime import UTC, date, datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


def _kickoff(day: date, hour: int = 15) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_weekend(session_factory: async_sessionmaker[AsyncSession]) -> Match:
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(
                name="Premier League",
                code="PL",
                start_date=date(2025, 8, 1),
            )
        )
        assert league.id is not None

        day = date(2026, 3, 14)
        history = [
            ("hist-1", "Arsenal", "Chelsea", 2, 1, day - timedelta(days=28)),
            ("hist-2", "Chelsea", "Arsenal", 0, 0, day - timedelta(days=21)),
            ("hist-3", "Arsenal", "Brighton", 3, 0, day - timedelta(days=14)),
            ("hist-4", "Liverpool", "Chelsea", 1, 0, day - timedelta(days=14)),
            ("hist-5", "Chelsea", "Everton", 2, 2, day - timedelta(days=7)),
            ("hist-6", "Arsenal", "Everton", 1, 0, day - timedelta(days=7)),
        ]
        for external_id, home, away, home_score, away_score, when in history:
            await ingest.upsert_match(
                Match(
                    external_id=external_id,
                    league_id=league.id,
                    match_date=_kickoff(when),
                    status=MatchStatus.FINISHED,
                    home_team=home,
                    away_team=away,
                    home_score=home_score,
                    away_score=away_score,
                    match_minute=90,
                )
            )

        fixture = await ingest.upsert_match(
            Match(
                external_id="pl-ars-che-2026",
                league_id=league.id,
                match_date=_kickoff(day),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        await ingest.upsert_match(
            Match(
                external_id="pl-liv-mci-2026",
                league_id=league.id,
                match_date=_kickoff(day, hour=17),
                status=MatchStatus.SCHEDULED,
                home_team="Liverpool",
                away_team="Man City",
            )
        )
        assert fixture.id is not None
        return fixture


def test_available_matches_and_match_detail(client: TestClient) -> None:
    fixture = anyio.run(_seed_weekend, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)

    select = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 1},
    )
    assert select.status_code == 200, select.text
    assert select.json()["use_match"] is True
    assert select.json()["game_number"] == 1

    available = client.get("/game/livematches", params={"matchDate": "14-03-2026"})
    assert available.status_code == 200, available.text
    payload = available.json()["matches"]
    assert len(payload) == 1
    assert payload[0]["home_team"] == "Arsenal"
    assert payload[0]["away_team"] == "Chelsea"
    assert payload[0]["game_number"] == 1

    admin_list = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
    )
    assert admin_list.status_code == 200
    assert len(admin_list.json()["matches"]) == 2

    detail = client.get(
        "/game/matchdetail",
        params={
            "hometeam": "Arsenal",
            "awayteam": "Chelsea",
            "matchdate": "14-03-2026",
        },
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["match_detail"]
    assert body["league_position"]["Arsenal"] == "1"
    assert body["league_position"]["Chelsea"] == "3"
    assert body["team_performance"]["Arsenal"] == ["WIN", "DRAW", "WIN", "WIN"]
    assert body["head_to_head"]["Arsenal"] == ["WIN", "DRAW"]
    assert body["head_to_head"]["Chelsea"] == ["LOSE", "DRAW"]

    async def update_score() -> Match:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            return await ingest.update_score(
                external_id="pl-ars-che-2026",
                status=MatchStatus.FINISHED,
                home_score=2,
                away_score=1,
                match_minute=90,
            )

    updated = anyio.run(update_score)
    assert updated.home_score == 2
    assert updated.use_match is True
    assert updated.game_number == 1


def test_match_writes_stamp_last_updated_utc(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from baked.superiorsix.repositories import matches as matches_repo

    clock = {"value": datetime(2026, 3, 14, 12, 0, tzinfo=UTC)}

    def fake_stamp() -> datetime:
        return clock["value"]

    monkeypatch.setattr(matches_repo, "_stamp_now", fake_stamp)

    async def run() -> None:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            league = await ingest.upsert_league(
                League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
            )
            assert league.id is not None

            created = await ingest.upsert_match(
                Match(
                    external_id="pl-freshness-1",
                    league_id=league.id,
                    match_date=_kickoff(date(2026, 3, 14)),
                    status=MatchStatus.SCHEDULED,
                    home_team="Arsenal",
                    away_team="Chelsea",
                )
            )
            assert created.last_updated == datetime(2026, 3, 14, 12, 0, tzinfo=UTC)
            assert created.last_updated.tzinfo is not None

            clock["value"] = datetime(2026, 3, 14, 12, 5, tzinfo=UTC)
            scored = await ingest.update_score(
                external_id="pl-freshness-1",
                status=MatchStatus.IN_PLAY,
                home_score=1,
                away_score=0,
                match_minute=12,
            )
            assert scored.last_updated == datetime(2026, 3, 14, 12, 5, tzinfo=UTC)

            clock["value"] = datetime(2026, 3, 14, 12, 10, tzinfo=UTC)
            matches = SqlAlchemyMatchRepository(session)
            leagues = LeagueService(SqlAlchemyLeagueRepository(session), matches)
            selected = await MatchService(matches, leagues).select_for_round(created.id, 1)
            assert selected.last_updated == datetime(2026, 3, 14, 12, 10, tzinfo=UTC)

    anyio.run(run)

    token, _ = make_access_token(roles=["game-master"])
    listed = client.get(
        "/admin/listmatches",
        headers=auth_header(token),
        params={"matchDate": "14-03-2026"},
    )
    assert listed.status_code == 200
    row = listed.json()["matches"][0]
    assert row["last_updated"] == "2026-03-14T12:10:00Z"


def test_livematches_requires_match_date(client: TestClient) -> None:
    response = client.get("/game/livematches")
    assert response.status_code == 400
    assert response.json()["detail"] == "missing matchDate"


def test_matchdetail_not_found(client: TestClient) -> None:
    response = client.get(
        "/game/matchdetail",
        params={"hometeam": "A", "awayteam": "B", "matchdate": "01-01-2026"},
    )
    assert response.status_code == 404


def test_drop_get_and_listmatches_range(client: TestClient) -> None:
    fixture = anyio.run(_seed_weekend, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)

    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 2},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["use_match"] is True
    assert selected.json()["game_number"] == 2

    fetched = client.get(
        "/admin/getmatch",
        headers=headers,
        params={"id": fixture.id},
    )
    assert fetched.status_code == 200
    assert fetched.json()["game_number"] == 2

    window = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": "01-03-2026", "toDate": "31-03-2026"},
    )
    assert window.status_code == 200, window.text
    ids = {row["id"] for row in window.json()["matches"]}
    assert fixture.id in ids

    same_day = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": "14-03-2026", "toDate": "14-03-2026"},
    )
    assert same_day.status_code == 200
    assert len(same_day.json()["matches"]) == 2

    legacy_day = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
    )
    assert legacy_day.status_code == 200
    assert len(legacy_day.json()["matches"]) == 2

    inverted = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": "31-03-2026", "toDate": "01-03-2026"},
    )
    assert inverted.status_code == 400

    # Deprecated alias still works.
    alias = client.get(
        "/admin/listmatchesnew",
        headers=headers,
        params={"fromDate": "01-03-2026", "toDate": "31-03-2026"},
    )
    assert alias.status_code == 200
    assert fixture.id in {row["id"] for row in alias.json()["matches"]}

    dropped = client.get(
        "/admin/dropmatch",
        headers=headers,
        params={"id": fixture.id},
    )
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["use_match"] is False
    assert dropped.json()["game_number"] is None

    available = client.get("/game/livematches", params={"matchDate": "14-03-2026"})
    assert available.status_code == 200
    assert available.json()["matches"] == []


def test_addmatches_replaces_day_selections(client: TestClient) -> None:
    fixture = anyio.run(_seed_weekend, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)

    listed = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
    )
    assert listed.status_code == 200
    day_matches = listed.json()["matches"]
    assert len(day_matches) == 2

    # Pad to six by selecting the same two fixtures is invalid — seed four more.
    async def seed_more() -> list[int]:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            league_id = day_matches[0]["league_id"]
            extras = []
            for index, (home, away, hour) in enumerate(
                (
                    ("Spurs", "Wolves", 12),
                    ("Everton", "Fulham", 12),
                    ("Brentford", "Palace", 14),
                    ("West Ham", "Forest", 14),
                ),
                start=1,
            ):
                match = await ingest.upsert_match(
                    Match(
                        external_id=f"pl-extra-{index}",
                        league_id=league_id,
                        match_date=_kickoff(date(2026, 3, 14), hour=hour),
                        status=MatchStatus.SCHEDULED,
                        home_team=home,
                        away_team=away,
                    )
                )
                assert match.id is not None
                extras.append(match.id)
            return extras

    extra_ids = anyio.run(seed_more)
    all_ids = [day_matches[0]["id"], day_matches[1]["id"], *extra_ids]
    payload = [
        {"id": match_id, "game_number": index}
        for index, match_id in enumerate(all_ids, start=1)
    ]

    replaced = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
        json=payload,
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()["matches"]
    assert len(body) == 6
    assert {row["game_number"] for row in body} == {1, 2, 3, 4, 5, 6}
    assert all(row["use_match"] is True for row in body)

    # Replace again with a shuffled set of the same six — still valid.
    reshuffled = [
        {"id": all_ids[i], "game_number": number}
        for i, number in enumerate([6, 5, 4, 3, 2, 1])
    ]
    again = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
        json=reshuffled,
    )
    assert again.status_code == 200, again.text
    assert again.json()["matches"][0]["game_number"] == 1

    # Partial batch with sparse slots (e.g. kept 2,4,6 after drops) is valid.
    sparse = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
        json=[
            {"id": all_ids[0], "game_number": 2},
            {"id": all_ids[1], "game_number": 4},
            {"id": all_ids[2], "game_number": 6},
        ],
    )
    assert sparse.status_code == 200, sparse.text
    assert {row["game_number"] for row in sparse.json()["matches"]} == {2, 4, 6}

    cleared = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
        json=[],
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["matches"] == []
    available = client.get("/game/livematches", params={"matchDate": "14-03-2026"})
    assert available.status_code == 200
    assert available.json()["matches"] == []


def test_selections_can_span_days_without_full_six(client: TestClient) -> None:
    """Fewer than six per day is OK; game_number slots stay unique across days."""
    fixture = anyio.run(_seed_weekend, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)

    listed = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
    )
    day_ids = [row["id"] for row in listed.json()["matches"]]
    assert len(day_ids) >= 2

    async def seed_next_day() -> int:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            league_id = listed.json()["matches"][0]["league_id"]
            match = await ingest.upsert_match(
                Match(
                    external_id="pl-next-day",
                    league_id=league_id,
                    match_date=_kickoff(date(2026, 3, 15), hour=15),
                    status=MatchStatus.SCHEDULED,
                    home_team="Newcastle",
                    away_team="Aston Villa",
                )
            )
            assert match.id is not None
            return match.id

    next_id = anyio.run(seed_next_day)

    first = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
        json=[
            {"id": day_ids[0], "game_number": 1},
            {"id": day_ids[1], "game_number": 2},
        ],
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["matches"]) == 2

    second = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "15-03-2026"},
        json=[{"id": next_id, "game_number": 3}],
    )
    assert second.status_code == 200, second.text
    assert second.json()["matches"][0]["game_number"] == 3

    # Reusing slot 1 on the next day clears it from the previous day.
    reclaim = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": "15-03-2026"},
        json=[{"id": next_id, "game_number": 1}],
    )
    assert reclaim.status_code == 200, reclaim.text
    day1 = client.get("/game/livematches", params={"matchDate": "14-03-2026"})
    assert day1.status_code == 200
    assert {row["game_number"] for row in day1.json()["matches"]} == {2}
    day2 = client.get("/game/livematches", params={"matchDate": "15-03-2026"})
    assert day2.status_code == 200
    assert day2.json()["matches"][0]["game_number"] == 1
