"""SS-16 gameday read APIs: meta, currentround, livescores."""

from datetime import UTC, date, datetime, timedelta
from functools import partial
from uuid import UUID

import anyio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.fakes import FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.gameday import prize_pot_pence
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_id: str,
    home: str,
    away: str,
    kickoff: datetime,
    status: str = MatchStatus.SCHEDULED,
    home_score: int | None = None,
    away_score: int | None = None,
) -> Match:
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        fixture = await ingest.upsert_match(
            Match(
                external_id=external_id,
                league_id=league.id,
                match_date=kickoff,
                status=status,
                home_team=home,
                away_team=away,
                home_score=home_score,
                away_score=away_score,
            )
        )
        assert fixture.id is not None
        return fixture


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def test_prize_pot_grows_with_slate_number() -> None:
    # 14 players × £1 buy-in → £14 / £28 / £42 across slates 1–3
    assert prize_pot_pence(buy_in_pence=100, n_players=14, round_number=1) == 1400
    assert prize_pot_pence(buy_in_pence=100, n_players=14, round_number=2) == 2800
    assert prize_pot_pence(buy_in_pence=100, n_players=14, round_number=3) == 4200
    # 14 × £2 → £28 / £56 / £84
    assert prize_pot_pence(buy_in_pence=200, n_players=14, round_number=1) == 2800
    assert prize_pot_pence(buy_in_pence=200, n_players=14, round_number=2) == 5600
    assert prize_pot_pence(buy_in_pence=200, n_players=14, round_number=3) == 8400


def test_meta_and_currentround_empty_without_open_round(client: TestClient) -> None:
    meta = client.get("/game/meta")
    assert meta.status_code == 200, meta.text
    body = meta.json()["meta"]
    assert body["round_number"] == 0
    assert body["prize_pot_pence"] == 0
    assert body["round"] is None
    assert body["subround"] is None

    current = client.get("/game/currentround")
    assert current.status_code == 200, current.text
    assert current.json() == {"current_round": None}


def test_meta_pot_uses_full_roster_and_slate_sequence(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    admin = _admin_headers()
    for i in range(3):
        auth_id = UUID(int=i + 1)
        user_profiles.seed(
            auth_id,
            first_name=f"P{i}",
            last_name="Player",
            email=f"p{i}@example.com",
        )

    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 200},
    )
    assert opened.status_code == 200, opened.text

    meta_before = client.get("/game/meta").json()["meta"]
    assert meta_before["round_number"] == 0
    assert meta_before["n_players"] == 3
    assert meta_before["prize_pot_pence"] == 0
    assert meta_before["buy_in_pence"] == 200

    kickoff = datetime.now(UTC) + timedelta(days=3)
    fixture = anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id="gameday-m1",
            home="Arsenal",
            away="Chelsea",
            kickoff=kickoff,
        )
    )
    selected = client.post(
        "/admin/addmatch",
        headers=admin,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    meta = client.get("/game/meta").json()["meta"]
    assert meta["round_number"] == 1
    assert meta["n_players"] == 3
    assert meta["prize_pot_pence"] == 200 * 3 * 1
    assert meta["subround"]["sequence"] == 1
    assert len(meta["subrounds"]) == 1
    assert len(meta["match_days"]) == 1

    current = client.get("/game/currentround").json()["current_round"]
    assert current["round_number"] == 1
    assert current["players"] == 3
    assert current["jackpot"] == 600
    assert current["prize_pot_pence"] == 600
    assert current["matches"] == 1
    assert current["buy_in_pence"] == 200


def test_livescores_scores_predictions_and_live_flag(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    admin = _admin_headers()
    kickoff = datetime.now(UTC) + timedelta(days=1)
    later = kickoff + timedelta(hours=2)
    fixture = anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id="gameday-live-1",
            home="Arsenal",
            away="Chelsea",
            kickoff=kickoff,
        )
    )
    future = anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id="gameday-live-2",
            home="Liverpool",
            away="Everton",
            kickoff=later,
        )
    )

    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text

    for match_id, game_number in ((fixture.id, 1), (future.id, 2)):
        selected = client.post(
            "/admin/addmatch",
            headers=admin,
            json={"id": match_id, "game_number": game_number},
        )
        assert selected.status_code == 200, selected.text
    subround_id = selected.json()["subround_id"]

    # Apply live score after both fixtures are on the same slate.
    async def _go_live(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as session:
            matches = MatchService(
                SqlAlchemyMatchRepository(session),
                LeagueService(
                    SqlAlchemyLeagueRepository(session),
                    SqlAlchemyMatchRepository(session),
                ),
            )
            await matches.apply_score_update(
                match_id=fixture.id,
                status=MatchStatus.IN_PLAY,
                home_score=1,
                away_score=0,
                match_minute=35,
            )

    anyio.run(_go_live, client.app.state.session_factory)

    _, ada_id = make_access_token(roles=["player"])
    _, bob_id = make_access_token(roles=["player"])
    user_profiles.seed(ada_id, first_name="Ada", last_name="Lovelace", nickname="Ada")
    user_profiles.seed(bob_id, first_name="Bob", last_name="Builder", nickname="Bob")

    assert (
        client.post(
            "/admin/addprediction",
            headers=admin,
            json={"auth_id": str(ada_id), "match_id": fixture.id, "prediction": "home"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/admin/addprediction",
            headers=admin,
            json={"auth_id": str(bob_id), "match_id": fixture.id, "prediction": "away"},
        ).status_code
        == 200
    )

    board = client.get("/game/livescores")
    assert board.status_code == 200, board.text
    body = board.json()
    assert body["match_count"] == 2, body
    assert body["subround"]["id"] == subround_id
    assert body["matches"][0]["status"] == MatchStatus.IN_PLAY, body["matches"]
    assert body["matches"][0]["home_score"] == 1
    assert body["matches"][0]["match_minute"] == 35
    assert body["matches"][0]["league_code"] == "PL"
    assert all(row["league_code"] == "PL" for row in body["matches"])
    assert body["live"] is True
    assert body["next_kickoff"] is not None
    assert len(body["scores"]) == 2
    for slate_match in body["matches"]:
        assert slate_match["difficulty"] is not None
        assert 0.0 <= slate_match["difficulty"]["score"] <= 1.0
        assert 1 <= slate_match["difficulty"]["band"] <= 5

    by_auth = {row["auth_id"]: row for row in body["scores"]}
    ada = by_auth[str(ada_id)]
    bob = by_auth[str(bob_id)]
    assert ada["score"] == 1
    assert bob["score"] == 0
    assert ada["matches"][0]["correct"] is True
    assert ada["matches"][0]["home_score"] == 1
    assert ada["matches"][0]["away_score"] == 0
    assert ada["matches"][0]["match_minute"] == 35
    assert ada["matches"][1]["home_score"] is None
    assert ada["matches"][1]["prediction"] is None
    assert bob["matches"][0]["correct"] is False
    assert bob["matches"][0]["match_minute"] == 35

    kickoff_day = kickoff.astimezone(UTC).strftime("%d-%m-%Y")
    later_day = later.astimezone(UTC).strftime("%d-%m-%Y")
    by_date = client.get("/game/livescores", params={"matchDate": kickoff_day})
    assert by_date.status_code == 200, by_date.text
    # Near UTC midnight the two kickoffs can land on different calendar days.
    expected_on_kickoff_day = 2 if kickoff_day == later_day else 1
    assert by_date.json()["match_count"] == expected_on_kickoff_day

    by_sub = client.get("/game/livescores", params={"subroundId": subround_id})
    assert by_sub.status_code == 200, by_sub.text
    assert by_sub.json()["match_count"] == 2
    assert by_sub.json()["matches"][0]["difficulty"] == body["matches"][0]["difficulty"]


def test_livescores_computes_match_minute_when_provider_null(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    """football-data live rows store null minute — derive from kickoff for FE n'."""
    admin = _admin_headers()
    kickoff = datetime.now(UTC) - timedelta(minutes=20)
    fixture = anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id="gameday-computed-minute",
            home="Spurs",
            away="West Ham",
            kickoff=kickoff,
        )
    )

    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text

    selected = client.post(
        "/admin/addmatch",
        headers=admin,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    async def _go_live(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as session:
            matches = MatchService(
                SqlAlchemyMatchRepository(session),
                LeagueService(
                    SqlAlchemyLeagueRepository(session),
                    SqlAlchemyMatchRepository(session),
                ),
            )
            await matches.apply_score_update(
                match_id=fixture.id,
                status=MatchStatus.IN_PLAY,
                home_score=0,
                away_score=0,
                match_minute=None,
            )

    anyio.run(_go_live, client.app.state.session_factory)

    _, player_id = make_access_token(roles=["player"])
    user_profiles.seed(player_id, first_name="Pat", last_name="Player", nickname="Pat")

    board = client.get("/game/livescores")
    assert board.status_code == 200, board.text
    body = board.json()
    assert body["matches"][0]["status"] == MatchStatus.IN_PLAY
    minute = body["matches"][0]["match_minute"]
    assert minute is not None
    assert 19 <= minute <= 23, minute
    assert body["scores"][0]["matches"][0]["match_minute"] == minute


def test_livescores_half_time_window_keeps_match_minute_null(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    admin = _admin_headers()
    kickoff = datetime.now(UTC) - timedelta(minutes=50)
    fixture = anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id="gameday-ht-null-minute",
            home="Brighton",
            away="Fulham",
            kickoff=kickoff,
        )
    )

    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text
    assert (
        client.post(
            "/admin/addmatch",
            headers=admin,
            json={"id": fixture.id, "game_number": 1},
        ).status_code
        == 200
    )

    async def _go_live(session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as session:
            matches = MatchService(
                SqlAlchemyMatchRepository(session),
                LeagueService(
                    SqlAlchemyLeagueRepository(session),
                    SqlAlchemyMatchRepository(session),
                ),
            )
            await matches.apply_score_update(
                match_id=fixture.id,
                status=MatchStatus.IN_PLAY,
                home_score=1,
                away_score=1,
                match_minute=None,
            )

    anyio.run(_go_live, client.app.state.session_factory)

    _, player_id = make_access_token(roles=["player"])
    user_profiles.seed(player_id, first_name="Pat", last_name="Player", nickname="Pat")

    board = client.get("/game/livescores")
    assert board.status_code == 200, board.text
    body = board.json()
    assert body["matches"][0]["match_minute"] is None
    assert body["scores"][0]["matches"][0]["match_minute"] is None
