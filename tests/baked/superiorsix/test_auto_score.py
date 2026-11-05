from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.process.extractors.auto_score import AutoScoreExtractor, decide_pull
from baked.superiorsix.process.extractors.connectors.dto import MatchPayload
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService

KICKOFF = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)


def test_decide_pull_before_kickoff() -> None:
    decision = decide_pull(
        now=KICKOFF - timedelta(minutes=5),
        match_date=KICKOFF,
        last_updated=None,
        status=MatchStatus.SCHEDULED,
    )
    assert decision.should_pull is False
    assert decision.reason == "before_kickoff"


def test_decide_pull_active_without_last_updated() -> None:
    decision = decide_pull(
        now=KICKOFF + timedelta(minutes=1),
        match_date=KICKOFF,
        last_updated=None,
        status=MatchStatus.SCHEDULED,
    )
    assert decision.should_pull is True
    assert decision.reason == "pull"


def test_decide_pull_recheck_wait() -> None:
    decision = decide_pull(
        now=KICKOFF + timedelta(minutes=10),
        match_date=KICKOFF,
        last_updated=KICKOFF + timedelta(minutes=9),
        status=MatchStatus.IN_PLAY,
    )
    assert decision.should_pull is False
    assert decision.reason == "recheck_wait"


def test_decide_pull_reported_half_time_pause() -> None:
    ht_at = KICKOFF + timedelta(minutes=47)
    decision = decide_pull(
        now=ht_at + timedelta(minutes=5),
        match_date=KICKOFF,
        last_updated=ht_at,
        status=MatchStatus.HALF_TIME,
    )
    assert decision.should_pull is False
    assert decision.reason == "ht_pause_reported"


def test_decide_pull_reported_half_time_resume() -> None:
    ht_at = KICKOFF + timedelta(minutes=47)
    decision = decide_pull(
        now=ht_at + timedelta(minutes=16),
        match_date=KICKOFF,
        last_updated=ht_at,
        status=MatchStatus.HALF_TIME,
    )
    assert decision.should_pull is True
    assert decision.reason == "pull"


def test_decide_pull_inferred_half_time_pause() -> None:
    decision = decide_pull(
        now=KICKOFF + timedelta(minutes=50),
        match_date=KICKOFF,
        last_updated=KICKOFF + timedelta(minutes=40),
        status=MatchStatus.IN_PLAY,
    )
    assert decision.should_pull is False
    assert decision.reason == "ht_pause_inferred"


def test_decide_pull_inferred_half_time_ends() -> None:
    decision = decide_pull(
        now=KICKOFF + timedelta(minutes=64),
        match_date=KICKOFF,
        last_updated=KICKOFF + timedelta(minutes=40),
        status=MatchStatus.IN_PLAY,
    )
    assert decision.should_pull is True
    assert decision.reason == "pull"


def test_decide_pull_terminal() -> None:
    decision = decide_pull(
        now=KICKOFF + timedelta(hours=2),
        match_date=KICKOFF,
        last_updated=KICKOFF,
        status=MatchStatus.FINISHED,
    )
    assert decision.should_pull is False
    assert decision.reason == "terminal"


class _RecordingLiveConnector:
    provider = "recording"

    def __init__(self) -> None:
        self.calls = 0

    def collect_leagues(self):
        return []

    def collect_matches(
        self, league_code: str, *, date_from, date_to, season_start=None, live=False
    ):
        assert league_code == "EL1"
        assert live is True
        self.calls += 1
        return [
            MatchPayload(
                external_id="ga-1",
                match_date=KICKOFF,
                status=MatchStatus.IN_PLAY,
                home_team="Barnsley",
                away_team="Bolton",
                home_score=1,
                away_score=0,
                match_minute=20,
            )
        ]


def _services(session: AsyncSession) -> tuple[MatchIngestService, LeagueService, MatchService]:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues), leagues, matches


@pytest.mark.asyncio
async def test_auto_score_skips_before_kickoff_and_pulls_when_due(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    clock = {"now": KICKOFF - timedelta(hours=6)}

    def _now() -> datetime:
        return clock["now"]

    recording = _RecordingLiveConnector()
    monkeypatch.setattr(
        "baked.superiorsix.process.extractors.match.connector_for_league",
        lambda *args, **kwargs: recording,
    )
    monkeypatch.setattr(
        "baked.superiorsix.repositories.matches._stamp_now",
        _now,
    )

    async with session_factory() as session:
        ingest, leagues, matches = _services(session)
        league = await ingest.upsert_league(
            League(name="League One", code="EL1", start_date=date(2025, 8, 1))
        )
        await ingest.upsert_match(
            Match(
                external_id="ga-1",
                league_id=league.id,  # type: ignore[arg-type]
                match_date=KICKOFF,
                status=MatchStatus.SCHEDULED,
                home_team="Barnsley",
                away_team="Bolton",
                use_match=True,
                game_number=1,
            )
        )
        # Not selected — ignored when use_match_only=True.
        await ingest.upsert_match(
            Match(
                external_id="ga-2",
                league_id=league.id,  # type: ignore[arg-type]
                match_date=KICKOFF,
                status=MatchStatus.SCHEDULED,
                home_team="Charlton",
                away_team="Derby",
                use_match=False,
            )
        )
        await session.commit()

        clock["now"] = KICKOFF + timedelta(minutes=3)
        extractor = AutoScoreExtractor(
            ingest,
            leagues,
            matches,
            football_data_token=None,
            goal_api_key="goal",
            use_match_only=True,
            now_fn=_now,
            sleep_fn=lambda *_: None,
        )
        stats = await extractor.process()
        await session.commit()

    assert stats["due"] == 1
    assert stats["leagues_pulled"] == 1
    assert recording.calls == 1

    # Fresh last_updated from the pull → next pass should skip recheck.
    async with session_factory() as session:
        ingest, leagues, matches = _services(session)
        clock["now"] = KICKOFF + timedelta(minutes=3, seconds=30)
        extractor = AutoScoreExtractor(
            ingest,
            leagues,
            matches,
            football_data_token=None,
            goal_api_key="goal",
            use_match_only=True,
            now_fn=_now,
            sleep_fn=lambda *_: None,
        )
        stats = await extractor.process()

    assert stats["leagues_pulled"] == 0
    assert stats["skipped"] >= 1
    assert recording.calls == 1


@pytest.mark.asyncio
async def test_auto_score_skips_inferred_ht_without_api_call(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    clock = {"now": KICKOFF + timedelta(minutes=40)}

    def _now() -> datetime:
        return clock["now"]

    recording = _RecordingLiveConnector()
    monkeypatch.setattr(
        "baked.superiorsix.process.extractors.match.connector_for_league",
        lambda *args, **kwargs: recording,
    )
    monkeypatch.setattr(
        "baked.superiorsix.repositories.matches._stamp_now",
        _now,
    )

    async with session_factory() as session:
        ingest, leagues, matches = _services(session)
        league = await ingest.upsert_league(
            League(name="League One", code="EL1", start_date=date(2025, 8, 1))
        )
        await ingest.upsert_match(
            Match(
                external_id="ga-1",
                league_id=league.id,  # type: ignore[arg-type]
                match_date=KICKOFF,
                status=MatchStatus.IN_PLAY,
                home_team="Barnsley",
                away_team="Bolton",
                use_match=True,
                game_number=1,
                home_score=0,
                away_score=0,
                match_minute=45,
            )
        )
        await session.commit()

        clock["now"] = KICKOFF + timedelta(minutes=50)
        extractor = AutoScoreExtractor(
            ingest,
            leagues,
            matches,
            football_data_token=None,
            goal_api_key="goal",
            now_fn=_now,
            sleep_fn=lambda *_: None,
        )
        stats = await extractor.process()

    assert stats["due"] == 0
    assert stats["leagues_pulled"] == 0
    assert recording.calls == 0
