from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import sleep

from baked.superiorsix.domain.models import Match, MatchStatus
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.match import MatchExtractor
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService

logger = logging.getLogger(__name__)

# Start actively pulling this far *after* registered kickoff (no pulls before).
DEFAULT_ACTIVE_AFTER_KICKOFF = timedelta(minutes=2)
# While live / in the active window, re-pull at most this often.
DEFAULT_RECHECK = timedelta(minutes=2)
# Half-time pause once HT is known (concrete API status or inferred).
DEFAULT_HT_PAUSE = timedelta(minutes=15)
# Infer HT at kickoff + 45 + 3 minutes added time when the API has no HT signal.
DEFAULT_HT_INFER_AFTER = timedelta(minutes=48)
# Live-candidate discovery around "now": match_date in
# [now - lookback, now + lookahead) ≡ now in [kickoff - 1h, kickoff + 4h].
DEFAULT_LOOKBACK = timedelta(hours=4)
DEFAULT_LOOKAHEAD = timedelta(hours=1)

_SCOREABLE_STATUSES = frozenset(
    {
        MatchStatus.SCHEDULED,
        MatchStatus.IN_PLAY,
        MatchStatus.HALF_TIME,
    }
)


@dataclass(frozen=True, slots=True)
class PullDecision:
    should_pull: bool
    reason: str


def decide_pull(
    *,
    now: datetime,
    match_date: datetime,
    last_updated: datetime | None,
    status: str,
    recheck: timedelta = DEFAULT_RECHECK,
    ht_pause: timedelta = DEFAULT_HT_PAUSE,
    ht_infer_after: timedelta = DEFAULT_HT_INFER_AFTER,
    active_after_kickoff: timedelta = DEFAULT_ACTIVE_AFTER_KICKOFF,
) -> PullDecision:
    """Decide whether a fixture should trigger a live score API request.

    Rules (UTC-aware ``match_date`` / ``last_updated``):
    - Finished / postponed → skip.
    - Before ``match_date + active_after_kickoff`` → skip (default: 2 minutes
      after registered kickoff; no pulls at or before kickoff).
    - Concrete half-time (``HALF TIME``): pause ``ht_pause`` from ``last_updated``.
    - Else infer HT at ``match_date + ht_infer_after`` and pause ``ht_pause`` from that.
    - Otherwise recheck at most every ``recheck`` since ``last_updated``.
    """
    now = _as_utc(now)
    kickoff = _as_utc(match_date)
    updated = _as_utc(last_updated) if last_updated is not None else None

    if status in {MatchStatus.FINISHED, MatchStatus.POSTPONED}:
        return PullDecision(False, "terminal")

    if now < kickoff + active_after_kickoff:
        return PullDecision(False, "before_active")

    if status == MatchStatus.HALF_TIME:
        if updated is not None and now < updated + ht_pause:
            return PullDecision(False, "ht_pause_reported")
    else:
        inferred_ht_start = kickoff + ht_infer_after
        if inferred_ht_start <= now < inferred_ht_start + ht_pause:
            return PullDecision(False, "ht_pause_inferred")

    if updated is not None and now < updated + recheck:
        return PullDecision(False, "recheck_wait")

    return PullDecision(True, "pull")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AutoScoreExtractor:
    """Smart live-score puller over selected (or all) fixtures.

    Orchestration may invoke this every ~15 minutes. Within a run (or optional
    ``max_run_seconds`` loop) API calls are skipped when cadence / half-time
    rules say wait — especially important for Goal API EL1/EL2 quotas.
    """

    def __init__(
        self,
        ingest: MatchIngestService,
        leagues: LeagueService,
        matches: MatchService,
        *,
        football_data_token: str | None,
        goal_api_key: str | None,
        use_match_only: bool = True,
        recheck: timedelta = DEFAULT_RECHECK,
        ht_pause: timedelta = DEFAULT_HT_PAUSE,
        ht_infer_after: timedelta = DEFAULT_HT_INFER_AFTER,
        active_after_kickoff: timedelta = DEFAULT_ACTIVE_AFTER_KICKOFF,
        lookback: timedelta = DEFAULT_LOOKBACK,
        lookahead: timedelta = DEFAULT_LOOKAHEAD,
        max_run_seconds: int = 0,
        http: JsonHttpClient | None = None,
        sleep_fn=sleep,
        now_fn=None,
    ) -> None:
        self._ingest = ingest
        self._leagues = leagues
        self._matches = matches
        self._football_data_token = football_data_token
        self._goal_api_key = goal_api_key
        self._use_match_only = use_match_only
        self._recheck = recheck
        self._ht_pause = ht_pause
        self._ht_infer_after = ht_infer_after
        self._active_after_kickoff = active_after_kickoff
        self._lookback = lookback
        self._lookahead = lookahead
        self._max_run_seconds = max(0, int(max_run_seconds))
        self._http = http
        self._sleep = sleep_fn
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    async def process(self) -> dict[str, int]:
        """Run one pass, or loop until ``max_run_seconds`` elapses."""
        started = self._now_fn()
        totals = {
            "candidates": 0,
            "due": 0,
            "skipped": 0,
            "leagues_pulled": 0,
            "matches_upserted": 0,
            "passes": 0,
        }
        while True:
            pass_stats = await self._run_once()
            totals["candidates"] = pass_stats["candidates"]
            totals["due"] += pass_stats["due"]
            totals["skipped"] += pass_stats["skipped"]
            totals["leagues_pulled"] += pass_stats["leagues_pulled"]
            totals["matches_upserted"] += pass_stats["matches_upserted"]
            totals["passes"] += 1

            if self._max_run_seconds <= 0:
                break
            if self._now_fn() >= started + timedelta(seconds=self._max_run_seconds):
                break
            self._sleep(min(self._recheck.total_seconds(), 60.0))
        return totals

    async def _run_once(self) -> dict[str, int]:
        now = self._now_fn()
        window_start = now - self._lookback
        window_end = now + self._lookahead
        candidates = await self._matches.list_score_candidates(
            window_start,
            window_end,
            use_match=True if self._use_match_only else None,
            statuses=_SCOREABLE_STATUSES,
        )
        league_by_id = {league.id: league for league in await self._leagues.list() if league.id}

        due_by_league: dict[str, list[Match]] = {}
        skipped = 0
        for match in candidates:
            decision = decide_pull(
                now=now,
                match_date=match.match_date,
                last_updated=match.last_updated,
                status=match.status,
                recheck=self._recheck,
                ht_pause=self._ht_pause,
                ht_infer_after=self._ht_infer_after,
                active_after_kickoff=self._active_after_kickoff,
            )
            if not decision.should_pull:
                skipped += 1
                logger.info(
                    "auto-score skip match=%s status=%s reason=%s",
                    match.external_id,
                    match.status,
                    decision.reason,
                )
                continue
            league = league_by_id.get(match.league_id)
            if league is None:
                logger.warning(
                    "auto-score missing league_id=%s for match=%s",
                    match.league_id,
                    match.external_id,
                )
                skipped += 1
                continue
            due_by_league.setdefault(league.code, []).append(match)

        matches_upserted = 0
        leagues_pulled = 0
        today = now.date()
        for league_code, due_matches in sorted(due_by_league.items()):
            logger.info(
                "auto-score pull league=%s due=%s",
                league_code,
                len(due_matches),
            )
            upserted = await self._pull_league(league_code, today)
            matches_upserted += len(upserted)
            leagues_pulled += 1

        return {
            "candidates": len(candidates),
            "due": sum(len(v) for v in due_by_league.values()),
            "skipped": skipped,
            "leagues_pulled": leagues_pulled,
            "matches_upserted": matches_upserted,
        }

    async def _pull_league(self, league_code: str, today: date) -> list[Match]:
        extractor = MatchExtractor(
            self._ingest,
            self._leagues,
            league_code=league_code,
            date_from=today,
            date_to=today,
            football_data_token=self._football_data_token,
            goal_api_key=self._goal_api_key,
            live=True,
            http=self._http,
        )
        return await extractor.process()
