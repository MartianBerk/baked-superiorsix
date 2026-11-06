from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.fakes import FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.exceptions import PredictionError
from baked.superiorsix.domain.models import League, Match, MatchStatus, Prediction, Round
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import (
    PredictionService,
    cluster_game_weeks,
    earliest_kickoff_for_game_week,
)


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_selected_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> Match:
    kickoff_day = date.today() + timedelta(days=7)
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        fixture = await ingest.upsert_match(
            Match(
                external_id="e2e-ars-che",
                league_id=league.id,
                match_date=datetime(
                    kickoff_day.year, kickoff_day.month, kickoff_day.day, 15, 0, tzinfo=UTC
                ),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        assert fixture.id is not None
        return fixture


def test_user_prediction_end_to_end(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    fixture = anyio.run(_seed_selected_fixture, client.app.state.session_factory)
    assert fixture.id is not None

    admin_token, _ = make_access_token(roles=["game-master"])
    admin = auth_header(admin_token)

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

    available = client.get(
        "/game/livematches",
        params={"matchDate": (date.today() + timedelta(days=7)).strftime("%d-%m-%Y")},
    )
    assert available.status_code == 200
    assert len(available.json()["matches"]) == 1

    player_token, auth_id = make_access_token(roles=["player"])
    user_profiles.seed(auth_id, first_name="Test", last_name="Player")
    player = auth_header(player_token)

    missing = client.get("/user/getprediction", headers=player, params={"gameId": fixture.id})
    assert missing.status_code == 200
    assert missing.json() == {"prediction": None}

    created = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "home"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["match_id"] == fixture.id
    assert body["prediction"] == "home"
    assert body["auth_id"] == str(auth_id)
    assert body["dropped"] is False

    fetched = client.get("/user/getprediction", headers=player, params={"gameId": fixture.id})
    assert fetched.status_code == 200
    assert fetched.json() == {"prediction": "home"}

    updated = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "draw"},
    )
    assert updated.status_code == 200
    assert updated.json()["prediction"] == "draw"
    assert updated.json()["id"] == body["id"]

    bad = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "win"},
    )
    assert bad.status_code == 400


def test_prediction_requires_player_role(client: TestClient) -> None:
    response = client.post(
        "/user/addprediction",
        json={"game_id": 1, "prediction": "home"},
    )
    assert response.status_code == 401


def _fixture(
    *,
    match_id: int,
    kickoff: datetime,
    game_number: int | None = 1,
    use_match: bool = True,
) -> Match:
    return Match(
        id=match_id,
        external_id=f"ext-{match_id}",
        league_id=1,
        match_date=kickoff,
        status=MatchStatus.SCHEDULED,
        home_team="Home",
        away_team="Away",
        use_match=use_match,
        game_number=game_number,
    )


def test_cluster_game_weeks_splits_on_gap_over_six_days() -> None:
    early = _fixture(match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1)
    same_week = _fixture(
        match_id=2, kickoff=datetime(2026, 3, 15, 14, 0, tzinfo=UTC), game_number=2
    )
    # 7 calendar days after same_week → new slate (gap > 6).
    next_week = _fixture(
        match_id=3, kickoff=datetime(2026, 3, 22, 15, 0, tzinfo=UTC), game_number=1
    )
    ignored = _fixture(
        match_id=99,
        kickoff=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        use_match=False,
        game_number=None,
    )

    weeks = cluster_game_weeks([next_week, ignored, same_week, early])
    assert [[m.id for m in week] for week in weeks] == [[1, 2], [3]]


def test_earliest_kickoff_uses_week_not_iso_monday() -> None:
    """Sat + following Fri stay one week; Monday ISO week would split them."""
    saturday = _fixture(
        match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1
    )
    friday = _fixture(
        match_id=2, kickoff=datetime(2026, 3, 20, 19, 45, tzinfo=UTC), game_number=2
    )
    assert earliest_kickoff_for_game_week(friday, [saturday, friday]) == saturday.match_date


class _FakePredictions:
    def __init__(self) -> None:
        self._by_key: dict[tuple[int, object, int], Prediction] = {}
        self._next_id = 1

    async def get_active(self, *, round_id: int, auth_id, match_id: int) -> Prediction | None:
        return self._by_key.get((round_id, auth_id, match_id))

    async def add(self, prediction: Prediction) -> Prediction:
        prediction.id = self._next_id
        self._next_id += 1
        self._by_key[(prediction.round_id, prediction.auth_id, prediction.match_id)] = prediction
        return prediction

    async def save(self, prediction: Prediction) -> Prediction:
        self._by_key[(prediction.round_id, prediction.auth_id, prediction.match_id)] = prediction
        return prediction


class _FakeRounds:
    def __init__(self, round_: Round) -> None:
        self._round = round_

    async def current(self) -> Round:
        return self._round

    async def current_or_none(self) -> Round | None:
        return self._round


class _FakeMatches:
    def __init__(self, matches: list[Match]) -> None:
        self._by_id = {match.id: match for match in matches if match.id is not None}

    async def get(self, match_id: int) -> Match:
        return self._by_id[match_id]

    async def selected_matches(self) -> list[Match]:
        return [m for m in self._by_id.values() if m.use_match]


def _service(matches: list[Match]) -> PredictionService:
    return PredictionService(
        _FakePredictions(),
        _FakeRounds(Round(id=1, start_date=date(2026, 1, 1), buy_in_pence=100)),
        _FakeMatches(matches),
    )


@pytest.mark.asyncio
async def test_submit_blocks_later_same_week_after_earliest_cutoff() -> None:
    earliest = _fixture(
        match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1
    )
    later = _fixture(
        match_id=2, kickoff=datetime(2026, 3, 15, 16, 30, tzinfo=UTC), game_number=2
    )
    service = _service([earliest, later])
    # 5 minutes before earliest kickoff → past 10m cutoff for the whole week.
    now = earliest.match_date - timedelta(minutes=5)

    with pytest.raises(PredictionError, match="Past cutoff for prediction set"):
        await service.submit(
            auth_id=uuid4(),
            match_id=later.id,
            prediction="home",
            now=now,
        )


@pytest.mark.asyncio
async def test_submit_earlier_week_does_not_lock_later_week() -> None:
    week1 = _fixture(
        match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1
    )
    week2 = _fixture(
        match_id=2, kickoff=datetime(2026, 3, 22, 15, 0, tzinfo=UTC), game_number=1
    )
    service = _service([week1, week2])
    # Past week1 cutoff, but still well before week2 earliest kickoff.
    now = week1.match_date + timedelta(hours=1)

    created = await service.submit(
        auth_id=uuid4(),
        match_id=week2.id,
        prediction="away",
        now=now,
    )
    assert created.prediction == "away"
    assert created.match_id == week2.id


@pytest.mark.asyncio
async def test_submit_single_match_week_uses_own_kickoff() -> None:
    alone = _fixture(
        match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1
    )
    service = _service([alone])

    ok = await service.submit(
        auth_id=uuid4(),
        match_id=alone.id,
        prediction="draw",
        now=alone.match_date - timedelta(minutes=11),
    )
    assert ok.prediction == "draw"

    with pytest.raises(PredictionError, match="Past cutoff for prediction set"):
        await service.submit(
            auth_id=uuid4(),
            match_id=alone.id,
            prediction="home",
            now=alone.match_date - timedelta(minutes=10),
        )
