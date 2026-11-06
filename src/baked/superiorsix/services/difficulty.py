"""Per-fixture prediction difficulty (SS-79).

Harder ≈ more evenly matched under weighted team signals. Pure functions only —
no I/O — so auto-selection (SS-31) can reuse the same scorer later.
"""

from __future__ import annotations

from dataclasses import dataclass

from baked.superiorsix.domain.models import TeamResult

# Relative weights from product rules (performance > table > H2H).
WEIGHT_PERFORMANCE = 0.50
WEIGHT_LEAGUE_POSITION = 0.30
WEIGHT_HEAD_TO_HEAD = 0.20

_RESULT_STRENGTH = {
    TeamResult.WIN: 1.0,
    TeamResult.DRAW: 0.5,
    TeamResult.LOSE: 0.0,
}

# Fallback when league table size is unknown (typical English top-flight size).
DEFAULT_LEAGUE_SIZE = 20

BAND_COUNT = 5


@dataclass(frozen=True, slots=True)
class MatchDifficulty:
    """Prediction difficulty for one fixture.

    ``score`` is in ``[0.0, 1.0]`` — higher means harder to predict.
    ``band`` is ``1..5`` (1 easiest, 5 hardest).
    """

    score: float
    band: int


def parse_league_position(raw: str | None) -> int | None:
    """Parse table position strings such as ``\"3\"`` or tied ``\"T1\"``."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text[0] in "Tt":
        text = text[1:]
    try:
        position = int(text)
    except ValueError:
        return None
    return position if position >= 1 else None


def form_strength(results: list[TeamResult]) -> float | None:
    """Mean result strength over recent form (WIN=1, DRAW=0.5, LOSE=0)."""
    if not results:
        return None
    return sum(_RESULT_STRENGTH[result] for result in results) / len(results)


def position_strength(position: int | None, *, league_size: int) -> float | None:
    """Map league place to ``[0, 1]`` (1st ≈ 1.0, last ≈ 0.0)."""
    if position is None or league_size < 2:
        return None
    capped = min(max(position, 1), league_size)
    return 1.0 - (capped - 1) / (league_size - 1)


def closeness(home: float, away: float) -> float:
    """How evenly matched two strengths are (1 = identical, 0 = maximally apart)."""
    return max(0.0, min(1.0, 1.0 - abs(home - away)))


def band_for_score(score: float) -> int:
    """Map ``[0, 1]`` score onto bands ``1..BAND_COUNT`` (higher = harder)."""
    clamped = max(0.0, min(1.0, score))
    if clamped >= 1.0:
        return BAND_COUNT
    return int(clamped * BAND_COUNT) + 1


def calculate_match_difficulty(
    *,
    home_performance: list[TeamResult],
    away_performance: list[TeamResult],
    home_position: str | int | None,
    away_position: str | int | None,
    home_head_to_head: list[TeamResult],
    away_head_to_head: list[TeamResult],
    league_size: int | None = None,
) -> MatchDifficulty:
    """Score fixture difficulty from the two teams' stats.

    Missing signals (no form / no table place / no H2H) drop out and their
    weight is redistributed across the signals that are present. If every
    signal is missing, return a mid-band neutral score (maximum uncertainty
    without evidence of a mismatch).
    """
    size = league_size if league_size is not None and league_size >= 2 else DEFAULT_LEAGUE_SIZE
    home_pos = (
        home_position
        if isinstance(home_position, int)
        else parse_league_position(home_position)
    )
    away_pos = (
        away_position
        if isinstance(away_position, int)
        else parse_league_position(away_position)
    )

    components: list[tuple[float, float]] = []

    home_form = form_strength(home_performance)
    away_form = form_strength(away_performance)
    if home_form is not None and away_form is not None:
        components.append((WEIGHT_PERFORMANCE, closeness(home_form, away_form)))

    home_table = position_strength(home_pos, league_size=size)
    away_table = position_strength(away_pos, league_size=size)
    if home_table is not None and away_table is not None:
        components.append((WEIGHT_LEAGUE_POSITION, closeness(home_table, away_table)))

    home_h2h = form_strength(home_head_to_head)
    away_h2h = form_strength(away_head_to_head)
    if home_h2h is not None and away_h2h is not None:
        components.append((WEIGHT_HEAD_TO_HEAD, closeness(home_h2h, away_h2h)))

    if not components:
        score = 0.5
    else:
        total_weight = sum(weight for weight, _ in components)
        score = sum(weight * value for weight, value in components) / total_weight

    score = round(score, 3)
    return MatchDifficulty(score=score, band=band_for_score(score))
