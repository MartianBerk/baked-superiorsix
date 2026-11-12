"""Tests for SS-229 automated match slate autopick."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.models import League, Match, MatchStatus, PredictionChoice
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.autopick import (
    AutopickReason,
    ScoredFixture,
    choose_kickoff_group,
    group_by_kickoff,
    is_preferred_saturday_1500,
    next_saturday,
    select_six_hierarchy_then_difficulty,
    target_difficulty_band,
)
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService

UK = ZoneInfo("Europe/London")


def _kickoff_uk(day: date, hour: int = 15, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UK).astimezone(UTC)


def _fixture(
    *,
    match_id: int,
    league_code: str,
    kickoff: datetime,
    band: int = 3,
    score: float = 0.5,
    external_id: str | None = None,
) -> ScoredFixture:
    return ScoredFixture(
        match=Match(
            id=match_id,
            external_id=external_id or f"ext-{match_id}",
            league_id=1,
            match_date=kickoff,
            status=MatchStatus.SCHEDULED,
            home_team=f"Home{match_id}",
            away_team=f"Away{match_id}",
            league_code=league_code,
            difficulty_band=band,
            difficulty_score=score,
        ),
        band=band,
        score=score,
        league_code=league_code,
    )


def test_target_difficulty_band_pointers_and_interpolation() -> None:
    assert target_difficulty_band(1) == 5.0
    assert target_difficulty_band(15) == 4.0
    assert target_difficulty_band(25) == 3.0
    assert target_difficulty_band(40) == 2.0
    assert target_difficulty_band(50) == 1.0
    assert target_difficulty_band(8) == pytest.approx(4.5)
    assert target_difficulty_band(0) == 5.0
    assert target_difficulty_band(100) == 1.0


def test_next_saturday_and_preferred_1500() -> None:
    assert next_saturday(date(2026, 11, 12)) == date(2026, 11, 14)  # Thu → Sat
    assert next_saturday(date(2026, 11, 14)) == date(2026, 11, 14)  # Sat stays
    sat = date(2026, 11, 14)
    preferred = _kickoff_uk(sat, 15, 0)
    other = _kickoff_uk(sat, 17, 30)
    assert is_preferred_saturday_1500(preferred) is True
    assert is_preferred_saturday_1500(other) is False


def test_choose_kickoff_prefers_saturday_1500_when_enough() -> None:
    sat = date(2026, 11, 14)
    k1500 = _kickoff_uk(sat, 15)
    k1730 = _kickoff_uk(sat, 17, 30)
    matches_1500 = [
        Match(
            id=i,
            external_id=f"a-{i}",
            league_id=1,
            match_date=k1500,
            status=MatchStatus.SCHEDULED,
            home_team="H",
            away_team="A",
        )
        for i in range(1, 7)
    ]
    matches_1730 = [
        Match(
            id=100 + i,
            external_id=f"b-{i}",
            league_id=1,
            match_date=k1730,
            status=MatchStatus.SCHEDULED,
            home_team="H",
            away_team="A",
        )
        for i in range(1, 8)
    ]
    groups = group_by_kickoff(matches_1500 + matches_1730)
    assert choose_kickoff_group(groups, day=sat) == k1500.astimezone(UTC)


def test_choose_kickoff_falls_back_when_1500_short() -> None:
    sat = date(2026, 11, 14)
    k1500 = _kickoff_uk(sat, 15)
    k1730 = _kickoff_uk(sat, 17, 30)
    short_1500 = [
        Match(
            id=i,
            external_id=f"a-{i}",
            league_id=1,
            match_date=k1500,
            status=MatchStatus.SCHEDULED,
            home_team="H",
            away_team="A",
        )
        for i in range(1, 4)
    ]
    full_1730 = [
        Match(
            id=100 + i,
            external_id=f"b-{i}",
            league_id=1,
            match_date=k1730,
            status=MatchStatus.SCHEDULED,
            home_team="H",
            away_team="A",
        )
        for i in range(1, 7)
    ]
    groups = group_by_kickoff(short_1500 + full_1730)
    assert choose_kickoff_group(groups, day=sat) == k1730.astimezone(UTC)


def test_hierarchy_prefers_pl_before_lower_leagues() -> None:
    kickoff = _kickoff_uk(date(2026, 11, 14), 15)
    # 4 PL + 4 ELC; target mid band. Must fill all 4 PL before any ELC.
    scored = [
        _fixture(match_id=1, league_code="PL", kickoff=kickoff, band=5),
        _fixture(match_id=2, league_code="PL", kickoff=kickoff, band=4),
        _fixture(match_id=3, league_code="PL", kickoff=kickoff, band=3),
        _fixture(match_id=4, league_code="PL", kickoff=kickoff, band=2),
        _fixture(match_id=5, league_code="ELC", kickoff=kickoff, band=5),
        _fixture(match_id=6, league_code="ELC", kickoff=kickoff, band=5),
        _fixture(match_id=7, league_code="ELC", kickoff=kickoff, band=5),
        _fixture(match_id=8, league_code="ELC", kickoff=kickoff, band=5),
    ]
    picked = select_six_hierarchy_then_difficulty(scored, target_band=5.0)
    assert len(picked) == 6
    codes = [item.league_code for item in picked]
    assert codes.count("PL") == 4
    assert codes.count("ELC") == 2
    assert all(item.match.id in {1, 2, 3, 4, 5, 6, 7, 8} for item in picked)
    # Among ELC, prefer hardest (band 5) — all are 5 so any 2 OK; PL all taken.


def test_hierarchy_then_closest_difficulty_within_league() -> None:
    kickoff = _kickoff_uk(date(2026, 11, 14), 15)
    scored = [
        _fixture(match_id=1, league_code="PL", kickoff=kickoff, band=5),
        _fixture(match_id=2, league_code="PL", kickoff=kickoff, band=1),
        _fixture(match_id=3, league_code="PL", kickoff=kickoff, band=3),
        _fixture(match_id=4, league_code="PL", kickoff=kickoff, band=2),
        _fixture(match_id=5, league_code="PL", kickoff=kickoff, band=4),
        _fixture(match_id=6, league_code="PL", kickoff=kickoff, band=5),
        _fixture(match_id=7, league_code="PL", kickoff=kickoff, band=1),
    ]
    # Target 3 → closest bands first: 3, then 2/4, then 1/5…
    picked = select_six_hierarchy_then_difficulty(scored, target_band=3.0)
    assert [item.match.id for item in picked] == [3, 4, 5, 1, 6, 2] or [
        item.match.id for item in picked
    ] == [3, 5, 4, 1, 6, 2]
    # id 3 (band 3) must be first; id 7 (extra band 1) excluded when 6 filled.
    assert picked[0].match.id == 3
    assert {item.match.id for item in picked} == {1, 2, 3, 4, 5, 6}


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def _player_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["player"])
    return auth_header(token)


def _open_round(client: TestClient, headers: dict[str, str]) -> None:
    opened = client.post(
        "/admin/addround",
        headers=headers,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 200},
    )
    assert opened.status_code == 200, opened.text


def test_autopick_requires_admin(client: TestClient) -> None:
    body = {"match_date": date.today().isoformat()}
    assert client.post("/admin/autopickmatches", json=body).status_code == 401
    forbidden = client.post(
        "/admin/autopickmatches",
        headers=_player_headers(),
        json=body,
    )
    assert forbidden.status_code == 403


def test_autopick_requires_match_date(client: TestClient) -> None:
    headers = _admin_headers()
    missing = client.post("/admin/autopickmatches", headers=headers, json={})
    assert missing.status_code == 422
    bare = client.post("/admin/autopickmatches", headers=headers)
    assert bare.status_code == 422


def test_autopick_noop_no_open_round(client: TestClient) -> None:
    response = client.post(
        "/admin/autopickmatches",
        headers=_admin_headers(),
        json={"match_date": date.today().isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "noop"
    assert body["reason"] == AutopickReason.NO_OPEN_ROUND
    assert body["matches"] == []


def test_autopick_noop_previous_missing(client: TestClient) -> None:
    headers = _admin_headers()
    _open_round(client, headers)
    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": date.today().isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "noop"
    assert body["reason"] == AutopickReason.PREVIOUS_SUBROUND_MISSING


def test_autopick_noop_previous_not_closed(client: TestClient) -> None:
    headers = _admin_headers()
    _open_round(client, headers)

    # Seed a scheduled (not finished) selected match as previous slate.
    async def _seed(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            day = date.today() + timedelta(days=7)
            season_start_year = day.year if day.month >= 8 else day.year - 1
            league = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert league.id is not None
            match = await ingest.upsert_match(
                Match(
                    external_id="open-slate-1",
                    league_id=league.id,
                    match_date=datetime.now(UTC) + timedelta(hours=2),
                    status=MatchStatus.SCHEDULED,
                    home_team="Arsenal",
                    away_team="Chelsea",
                )
            )
            await session.commit()
            return match

    fixture = anyio.run(_seed, client.app.state.session_factory)
    assert fixture.id is not None
    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": date.today().isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "noop"
    assert body["reason"] == AutopickReason.PREVIOUS_SUBROUND_NOT_CLOSED


def test_autopick_selects_six_same_kickoff_preferring_hierarchy(
    client: TestClient,
) -> None:
    headers = _admin_headers()
    _open_round(client, headers)

    # Freeze "now" indirectly by choosing a known next Saturday relative to today.
    today_uk = datetime.now(UK).date()
    saturday = next_saturday(today_uk)
    # If today is Saturday and we're past the window logic, next_saturday returns today —
    # candidates kick off later that day in UK time.
    candidate_kickoff = _kickoff_uk(saturday, 15, 0)
    previous_day = saturday - timedelta(days=7)
    previous_kickoff = _kickoff_uk(previous_day, 15, 0)

    async def _seed(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            season_start_year = saturday.year if saturday.month >= 8 else saturday.year - 1
            pl = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            elc = await ingest.upsert_league(
                League(
                    name="Championship",
                    code="ELC",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert pl.id is not None and elc.id is not None
            previous = await ingest.upsert_match(
                Match(
                    external_id="prev-finished",
                    league_id=pl.id,
                    match_date=previous_kickoff,
                    status=MatchStatus.FINISHED,
                    home_team="Arsenal",
                    away_team="Chelsea",
                    home_score=2,
                    away_score=1,
                    match_minute=90,
                )
            )
            cand_ids: list[int] = []
            for index in range(6):
                league_id = pl.id if index < 4 else elc.id
                row = await ingest.upsert_match(
                    Match(
                        external_id=f"sat-cand-{index}",
                        league_id=league_id,
                        match_date=candidate_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"Home{index}",
                        away_team=f"Away{index}",
                    )
                )
                assert row.id is not None
                cand_ids.append(row.id)
            # Extra later kickoff with enough fixtures must lose to 15:00.
            later = _kickoff_uk(saturday, 17, 30)
            for index in range(6):
                await ingest.upsert_match(
                    Match(
                        external_id=f"sat-late-{index}",
                        league_id=pl.id,
                        match_date=later,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"LateH{index}",
                        away_team=f"LateA{index}",
                    )
                )
            await session.commit()
            return previous, cand_ids

    previous, _cand_ids = anyio.run(_seed, client.app.state.session_factory)
    assert previous.id is not None

    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": previous.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    # Mark previous finished again after select (select freezes difficulty; status stays).
    async def _finish(session_factory):
        async with session_factory() as session:
            matches = MatchService(
                SqlAlchemyMatchRepository(session),
                LeagueService(
                    SqlAlchemyLeagueRepository(session),
                    SqlAlchemyMatchRepository(session),
                ),
            )
            match = await matches.get(previous.id)
            match.status = MatchStatus.FINISHED
            match.home_score = 2
            match.away_score = 1
            match.match_minute = 90
            await matches._matches.save(match)
            await session.commit()

    anyio.run(_finish, client.app.state.session_factory)

    # Advance clock past previous kickoff so subround is_over and editing advances.
    # Autopick gates on FINISHED, not is_over; require_open_for_selection uses is_over.
    # Force-open next empty subround so editing target is ready (simulates post-match).
    # When previous is over by kickoff time: if previous_kickoff is in the past, is_over.
    # Our previous_kickoff is last Saturday — usually past. If not (clock skew), force open.
    opened = client.post("/admin/opensubround", headers=headers)
    # opensubround always creates next; if previous still "not over" by kickoff, we still
    # have finished gate. Autopick evaluates statuses[-2] when latest is empty.
    assert opened.status_code == 200, opened.text

    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": saturday.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "selected", body
    assert body["reason"] is None
    assert len(body["matches"]) == 6
    assert body["subround"] is not None
    assert body["subround"]["match_count"] == 6
    kickoffs = {match["match_date"] for match in body["matches"]}
    assert len(kickoffs) == 1
    codes = [match["league_code"] for match in body["matches"]]
    assert codes.count("PL") == 4
    assert codes.count("ELC") == 2
    assert body["target_difficulty_band"] is not None
    game_numbers = [match["game_number"] for match in body["matches"]]
    assert game_numbers == [1, 2, 3, 4, 5, 6]


def test_autopick_noop_perfect_slate(client: TestClient) -> None:
    headers = _admin_headers()
    _open_round(client, headers)
    _, player_auth = make_access_token(roles=["player"])

    async def _seed(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            day = date.today() - timedelta(days=3)
            season_start_year = day.year if day.month >= 8 else day.year - 1
            league = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert league.id is not None
            match = await ingest.upsert_match(
                Match(
                    external_id="perfect-1",
                    league_id=league.id,
                    match_date=datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC),
                    status=MatchStatus.FINISHED,
                    home_team="Arsenal",
                    away_team="Chelsea",
                    home_score=1,
                    away_score=0,
                    match_minute=90,
                )
            )
            await session.commit()
            return match

    fixture = anyio.run(_seed, client.app.state.session_factory)
    assert fixture.id is not None

    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    # Ensure finished after select.
    async def _finish(session_factory):
        async with session_factory() as session:
            repo = SqlAlchemyMatchRepository(session)
            match = await repo.get(fixture.id)
            assert match is not None
            match.status = MatchStatus.FINISHED
            match.home_score = 1
            match.away_score = 0
            match.match_minute = 90
            await repo.save(match)
            await session.commit()

    anyio.run(_finish, client.app.state.session_factory)

    # Player submits correct home pick (admin path bypasses cutoff).
    pred = client.post(
        "/admin/addprediction",
        headers=headers,
        json={
            "auth_id": str(player_auth),
            "match_id": fixture.id,
            "prediction": PredictionChoice.HOME,
        },
    )
    # Prefer bulk if single endpoint differs — check status.
    if pred.status_code == 404:
        pred = client.post(
            "/admin/addpredictions",
            headers=headers,
            json=[
                {
                    "auth_id": str(player_auth),
                    "match_id": fixture.id,
                    "prediction": "home",
                }
            ],
        )
    assert pred.status_code == 200, pred.text

    # Empty next subround so gates look at previous.
    opened = client.post("/admin/opensubround", headers=headers)
    assert opened.status_code == 200, opened.text

    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": date.today().isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "noop"
    assert body["reason"] == AutopickReason.PERFECT_SLATE_EXISTS


def _prepare_finished_previous_and_open_next(
    client: TestClient,
    headers: dict[str, str],
    *,
    previous_kickoff: datetime,
) -> None:
    """Seed one finished selected match, then force-open an empty next subround."""

    async def _seed(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            day = previous_kickoff.astimezone(UK).date()
            season_start_year = day.year if day.month >= 8 else day.year - 1
            league = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert league.id is not None
            match = await ingest.upsert_match(
                Match(
                    external_id="gate-prev-finished",
                    league_id=league.id,
                    match_date=previous_kickoff,
                    status=MatchStatus.FINISHED,
                    home_team="Arsenal",
                    away_team="Chelsea",
                    home_score=2,
                    away_score=1,
                    match_minute=90,
                )
            )
            await session.commit()
            return match

    previous = anyio.run(_seed, client.app.state.session_factory)
    assert previous.id is not None
    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": previous.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    async def _finish(session_factory):
        async with session_factory() as session:
            repo = SqlAlchemyMatchRepository(session)
            match = await repo.get(previous.id)
            assert match is not None
            match.status = MatchStatus.FINISHED
            match.home_score = 2
            match.away_score = 1
            match.match_minute = 90
            await repo.save(match)
            await session.commit()

    anyio.run(_finish, client.app.state.session_factory)
    opened = client.post("/admin/opensubround", headers=headers)
    assert opened.status_code == 200, opened.text


def test_api_autopick_uses_requested_day_not_next_saturday(client: TestClient) -> None:
    """Admin API must stay on match_date even when a later Saturday also has 6+."""
    headers = _admin_headers()
    _open_round(client, headers)

    thursday = date(2026, 11, 12)  # fixed non-Saturday
    saturday = date(2026, 11, 14)
    assert thursday.weekday() == 3
    assert saturday.weekday() == 5
    previous_kickoff = _kickoff_uk(thursday - timedelta(days=7), 15)
    thu_kickoff = _kickoff_uk(thursday, 15)
    sat_kickoff = _kickoff_uk(saturday, 15)

    _prepare_finished_previous_and_open_next(
        client, headers, previous_kickoff=previous_kickoff
    )

    async def _seed_candidates(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            season_start_year = thursday.year if thursday.month >= 8 else thursday.year - 1
            pl = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert pl.id is not None
            for index in range(6):
                await ingest.upsert_match(
                    Match(
                        external_id=f"thu-cand-{index}",
                        league_id=pl.id,
                        match_date=thu_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"ThuH{index}",
                        away_team=f"ThuA{index}",
                    )
                )
            for index in range(6):
                await ingest.upsert_match(
                    Match(
                        external_id=f"sat-cand-extra-{index}",
                        league_id=pl.id,
                        match_date=sat_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"SatH{index}",
                        away_team=f"SatA{index}",
                    )
                )
            await session.commit()

    anyio.run(_seed_candidates, client.app.state.session_factory)

    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": thursday.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "selected", body
    assert len(body["matches"]) == 6
    for match in body["matches"]:
        assert match["home_team"].startswith("ThuH")
    assert (
        datetime.fromisoformat(body["kickoff"].replace("Z", "+00:00")) == thu_kickoff
    )


def test_api_autopick_does_not_lookahead_when_day_insufficient(
    client: TestClient,
) -> None:
    """If match_date lacks a ≥6 kickoff group, API no-ops even if Saturday has one."""
    headers = _admin_headers()
    _open_round(client, headers)

    thursday = date(2026, 11, 12)
    saturday = date(2026, 11, 14)
    previous_kickoff = _kickoff_uk(thursday - timedelta(days=7), 15)
    thu_kickoff = _kickoff_uk(thursday, 15)
    sat_kickoff = _kickoff_uk(saturday, 15)

    _prepare_finished_previous_and_open_next(
        client, headers, previous_kickoff=previous_kickoff
    )

    async def _seed_candidates(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            season_start_year = thursday.year if thursday.month >= 8 else thursday.year - 1
            pl = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert pl.id is not None
            # Only 3 on Thursday — insufficient for API day scope.
            for index in range(3):
                await ingest.upsert_match(
                    Match(
                        external_id=f"thu-short-{index}",
                        league_id=pl.id,
                        match_date=thu_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"ShortH{index}",
                        away_team=f"ShortA{index}",
                    )
                )
            for index in range(6):
                await ingest.upsert_match(
                    Match(
                        external_id=f"sat-full-{index}",
                        league_id=pl.id,
                        match_date=sat_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"FullH{index}",
                        away_team=f"FullA{index}",
                    )
                )
            await session.commit()

    anyio.run(_seed_candidates, client.app.state.session_factory)

    response = client.post(
        "/admin/autopickmatches",
        headers=headers,
        json={"match_date": thursday.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "noop"
    assert body["reason"] == AutopickReason.INSUFFICIENT_SAME_KICKOFF


def test_cli_lookahead_picks_next_saturday_without_match_date(client: TestClient) -> None:
    """Service/CLI path (match_date=None) looks ahead to next Saturday from now."""
    headers = _admin_headers()
    _open_round(client, headers)

    # Wednesday → next Saturday is +3 days.
    wednesday = date(2026, 11, 11)
    saturday = date(2026, 11, 14)
    assert next_saturday(wednesday) == saturday
    previous_kickoff = _kickoff_uk(saturday - timedelta(days=7), 15)
    sat_kickoff = _kickoff_uk(saturday, 15)

    _prepare_finished_previous_and_open_next(
        client, headers, previous_kickoff=previous_kickoff
    )

    async def _seed_and_run(session_factory):
        async with session_factory() as session:
            ingest = _ingest(session)
            season_start_year = saturday.year if saturday.month >= 8 else saturday.year - 1
            pl = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert pl.id is not None
            for index in range(6):
                await ingest.upsert_match(
                    Match(
                        external_id=f"cli-sat-{index}",
                        league_id=pl.id,
                        match_date=sat_kickoff,
                        status=MatchStatus.SCHEDULED,
                        home_team=f"CliH{index}",
                        away_team=f"CliA{index}",
                    )
                )
            await session.commit()

            from baked.superiorsix.repositories.predictions import (
                SqlAlchemyPredictionRepository,
            )
            from baked.superiorsix.repositories.rounds import SqlAlchemyRoundRepository
            from baked.superiorsix.repositories.subrounds import (
                SqlAlchemySubroundRepository,
            )
            from baked.superiorsix.services.autopick import AutopickService
            from baked.superiorsix.services.predictions import PredictionService
            from baked.superiorsix.services.rounds import RoundService
            from baked.superiorsix.services.subrounds import SubroundService

            leagues = LeagueService(
                SqlAlchemyLeagueRepository(session),
                SqlAlchemyMatchRepository(session),
            )
            matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
            rounds = RoundService(SqlAlchemyRoundRepository(session))
            subrounds = SubroundService(
                SqlAlchemySubroundRepository(session),
                SqlAlchemyMatchRepository(session),
                rounds,
            )
            predictions = PredictionService(
                SqlAlchemyPredictionRepository(session),
                rounds,
                matches,
            )
            autopick = AutopickService(
                rounds=rounds,
                subrounds=subrounds,
                matches=matches,
                predictions=predictions,
            )
            # No match_date → look ahead from Wednesday to Saturday.
            now = datetime(2026, 11, 11, 12, 0, tzinfo=UK)
            result = await autopick.run(now=now, match_date=None)
            await session.commit()
            return result

    result = anyio.run(_seed_and_run, client.app.state.session_factory)
    assert result.status == "selected", result.message
    assert len(result.matches) == 6
    assert all(match.home_team.startswith("CliH") for match in result.matches)
    assert result.kickoff == sat_kickoff.astimezone(UTC)
