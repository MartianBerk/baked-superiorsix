"""Euro Wizard scoring — port of legacy ``EUROS_SCORES`` view.

Base point: correct home / away / draw against stored full-time scores.
Knockout bonus (``matchday >= 4``) only when the base point is earned:

* pens pick + match went to pens → +3
* ET pick + match went to ET (not the pens branch) → +2
* 90' pick + match finished in 90 → +1
* otherwise → +0

Group-stage matchdays (1–3) never award bonus.
"""

from __future__ import annotations

from baked.superiorsix.domain.models import (
    EurosMatch,
    EurosPrediction,
    MatchStatus,
    PredictionChoice,
)

# Legacy: ``WHEN [m].[matchday] < 4 THEN 0`` — group stage has no ET/pens bonus.
GROUP_STAGE_MAX_MATCHDAY = 3


def base_score(prediction: str, match: EurosMatch) -> int:
    """1 when the outcome pick matches finished full-time scores, else 0."""
    if match.status != MatchStatus.FINISHED:
        return 0
    if match.home_score is None or match.away_score is None:
        return 0
    choice = prediction.strip().lower()
    home = match.home_score
    away = match.away_score
    if choice == PredictionChoice.HOME and home > away:
        return 1
    if choice == PredictionChoice.AWAY and away > home:
        return 1
    if choice == PredictionChoice.DRAW and home == away:
        return 1
    return 0


def knockout_bonus(prediction: EurosPrediction, match: EurosMatch) -> int:
    """Raw knockout bonus for one finished match (before base-score gate)."""
    if match.matchday <= GROUP_STAGE_MAX_MATCHDAY:
        return 0
    match_et = bool(match.extra_time)
    match_pens = bool(match.penalties)
    if prediction.plus_ninety and prediction.penalties and match_pens:
        return 3
    if prediction.plus_ninety and prediction.extra_time and match_et:
        return 2
    if (not prediction.plus_ninety) and (not match_et) and (not match_pens):
        return 1
    return 0


def score_prediction(prediction: EurosPrediction, match: EurosMatch) -> tuple[int, int]:
    """Return ``(score, bonus)`` for one prediction against a finished match.

    Bonus is zeroed when the base score is 0 (legacy ``CASE WHEN score > 0``).
    """
    score = base_score(prediction.prediction, match)
    raw_bonus = knockout_bonus(prediction, match)
    bonus = raw_bonus if score > 0 else 0
    return score, bonus
