from datetime import UTC, date, datetime, timedelta

import anyio
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


async def _seed_fixtures(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    base_day: date | None = None,
) -> tuple[int, int, int, date, date, date]:
    """Return (sat_id, mon_id, wed_id, sat_day, mon_day, wed_day)."""
    sat_day = base_day or (date.today() + timedelta(days=7))
    # Keep Sat/Mon/Wed within one calendar week but distinct days.
    mon_day = sat_day + timedelta(days=2)
    wed_day = sat_day + timedelta(days=4)
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        sat = await ingest.upsert_match(
            Match(
                external_id="sub-sat",
                league_id=league.id,
                match_date=_kickoff(sat_day),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        mon = await ingest.upsert_match(
            Match(
                external_id="sub-mon",
                league_id=league.id,
                match_date=_kickoff(mon_day),
                status=MatchStatus.SCHEDULED,
                home_team="Liverpool",
                away_team="Everton",
            )
        )
        wed = await ingest.upsert_match(
            Match(
                external_id="sub-wed",
                league_id=league.id,
                match_date=_kickoff(wed_day),
                status=MatchStatus.SCHEDULED,
                home_team="Spurs",
                away_team="Wolves",
            )
        )
        assert sat.id and mon.id and wed.id
        return sat.id, mon.id, wed.id, sat_day, mon_day, wed_day


def _fmt(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def _admin(client: TestClient) -> dict[str, str]:
    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)
    opened = client.post(
        "/admin/addround",
        headers=headers,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text
    return headers


def test_no_round_listmatches_ok_subround_paths_clear(client: TestClient) -> None:
    """Fixtures list without a Round; subround open/create requires a Round."""
    sat_id, _, _, sat_day, _, _ = anyio.run(_seed_fixtures, client.app.state.session_factory)
    token, _ = make_access_token(roles=["game-master"])
    headers = auth_header(token)

    listed = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": _fmt(sat_day), "toDate": _fmt(sat_day)},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["matches"]) >= 1

    assert client.get("/admin/listrounds", headers=headers).json()["rounds"] == []

    current = client.get("/admin/currentsubround", headers=headers)
    assert current.status_code == 200
    assert current.json()["current_subround"] is None

    subrounds = client.get("/admin/listsubrounds", headers=headers)
    assert subrounds.status_code == 200
    assert subrounds.json()["subrounds"] == []

    create = client.get(
        "/admin/currentsubround",
        headers=headers,
        params={"create": "true"},
    )
    assert create.status_code == 400
    assert create.json()["detail"] == "No round in progress."

    opened = client.post("/admin/opensubround", headers=headers)
    assert opened.status_code == 400
    assert opened.json()["detail"] == "No round in progress."

    submitted = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(sat_day)},
        json=[{"id": sat_id, "game_number": 1}],
    )
    assert submitted.status_code == 400
    assert submitted.json()["detail"] == "No round in progress."

    player = client.get("/game/listsubrounds")
    assert player.status_code == 200
    assert player.json()["subrounds"] == []


def test_addmatches_attaches_to_open_subround(client: TestClient) -> None:
    sat_id, mon_id, _, sat_day, mon_day, _ = anyio.run(
        _seed_fixtures, client.app.state.session_factory
    )
    headers = _admin(client)

    current = client.get("/admin/currentsubround", headers=headers)
    assert current.status_code == 200
    assert current.json()["current_subround"] is not None
    assert current.json()["current_subround"]["sequence"] == 1
    assert current.json()["current_subround"]["match_count"] == 0

    first = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(sat_day)},
        json=[{"id": sat_id, "game_number": 1}],
    )
    assert first.status_code == 200, first.text
    body = first.json()["matches"]
    assert len(body) == 1
    subround_id = body[0]["subround_id"]
    assert subround_id is not None

    listed = client.get("/admin/listsubrounds", headers=headers)
    assert listed.status_code == 200
    subrounds = listed.json()["subrounds"]
    assert len(subrounds) == 1
    assert subrounds[0]["id"] == subround_id
    assert subrounds[0]["sequence"] == 1
    assert subrounds[0]["match_count"] == 1
    assert subrounds[0]["is_open_for_editing"] is True
    assert subrounds[0]["is_over"] is False

    # Same open subround receives Monday submit (still before max kickoff).
    second = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(mon_day)},
        json=[{"id": mon_id, "game_number": 2}],
    )
    assert second.status_code == 200, second.text
    assert second.json()["matches"][0]["subround_id"] == subround_id

    listed = client.get("/admin/listsubrounds", headers=headers)
    assert listed.json()["subrounds"][0]["match_count"] == 2


def test_force_open_subround_separates_slates(client: TestClient) -> None:
    """Admin can open Sat / Mon as separate subrounds before either kicks off."""
    sat_id, mon_id, wed_id, sat_day, mon_day, wed_day = anyio.run(
        _seed_fixtures, client.app.state.session_factory
    )
    headers = _admin(client)

    sat = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(sat_day)},
        json=[{"id": sat_id, "game_number": 1}],
    )
    assert sat.status_code == 200
    sat_sub = sat.json()["matches"][0]["subround_id"]

    opened = client.post("/admin/opensubround", headers=headers)
    assert opened.status_code == 200, opened.text
    assert opened.json()["sequence"] == 2
    assert opened.json()["is_open_for_editing"] is True
    assert opened.json()["match_count"] == 0

    mon = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(mon_day)},
        json=[{"id": mon_id, "game_number": 1}],
    )
    assert mon.status_code == 200
    mon_sub = mon.json()["matches"][0]["subround_id"]
    assert mon_sub != sat_sub

    # Slot 1 reuse in the new subround does not clear Saturday's selection.
    livematches = client.get("/game/livematches", params={"matchDate": _fmt(sat_day)})
    assert livematches.status_code == 200
    assert len(livematches.json()["matches"]) == 1
    assert livematches.json()["matches"][0]["subround_id"] == sat_sub

    client.post("/admin/opensubround", headers=headers)
    wed = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(wed_day)},
        json=[{"id": wed_id, "game_number": 1}],
    )
    assert wed.status_code == 200
    assert wed.json()["matches"][0]["subround_id"] not in {sat_sub, mon_sub}

    listed = client.get("/admin/listsubrounds", headers=headers)
    assert [row["sequence"] for row in listed.json()["subrounds"]] == [1, 2, 3]

    player_view = client.get("/game/listsubrounds")
    assert player_view.status_code == 200
    assert len(player_view.json()["subrounds"]) == 3


def test_currentsubround_create_when_previous_over(client: TestClient) -> None:
    past_day = date.today() - timedelta(days=3)
    async def seed_past():
        return await _seed_fixtures(
            client.app.state.session_factory,
            base_day=past_day,
        )

    sat_id, _, _, sat_day, _, _ = anyio.run(seed_past)
    headers = _admin(client)

    selected = client.post(
        "/admin/addmatches",
        headers=headers,
        params={"matchDate": _fmt(sat_day)},
        json=[{"id": sat_id, "game_number": 1}],
    )
    assert selected.status_code == 200
    first_id = selected.json()["matches"][0]["subround_id"]

    current = client.get("/admin/currentsubround", headers=headers)
    assert current.status_code == 200
    assert current.json()["current_subround"] is None

    created = client.get(
        "/admin/currentsubround",
        headers=headers,
        params={"create": "true"},
    )
    assert created.status_code == 200, created.text
    body = created.json()["current_subround"]
    assert body is not None
    assert body["sequence"] == 2
    assert body["id"] != first_id
    assert body["is_open_for_editing"] is True
    assert body["match_count"] == 0
