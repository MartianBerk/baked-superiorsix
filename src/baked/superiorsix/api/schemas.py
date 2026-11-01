from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from baked.superiorsix.domain.models import (
    League,
    Match,
    MatchDetail,
    Prediction,
    Round,
    TeamResult,
)


class AvailableMatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    status: str
    match_minute: int | None = None
    game_number: int | None = None
    match_date: datetime

    @classmethod
    def from_domain(cls, match: Match) -> "AvailableMatchResponse":
        assert match.id is not None
        return cls(
            id=match.id,
            home_team=match.home_team,
            away_team=match.away_team,
            home_score=match.home_score,
            away_score=match.away_score,
            status=match.status,
            match_minute=match.match_minute,
            game_number=match.game_number,
            match_date=match.match_date,
        )


class AvailableMatchesResponse(BaseModel):
    matches: list[AvailableMatchResponse]


class MatchDetailPayload(BaseModel):
    league_position: dict[str, str | None]
    team_performance: dict[str, list[TeamResult]]
    head_to_head: dict[str, list[TeamResult]]

    @classmethod
    def from_domain(cls, detail: MatchDetail) -> "MatchDetailPayload":
        return cls(
            league_position=detail.league_position,
            team_performance=detail.team_performance,
            head_to_head=detail.head_to_head,
        )


class MatchDetailResponse(BaseModel):
    match_detail: MatchDetailPayload


class SelectMatchRequest(BaseModel):
    id: int = Field(description="Match id to select for Super Six")
    game_number: int = Field(ge=1, le=6)


class LeagueResponse(BaseModel):
    id: int
    name: str
    code: str
    start_date: date
    current_matchday: int | None = None

    @classmethod
    def from_domain(cls, league: League) -> "LeagueResponse":
        assert league.id is not None
        return cls(
            id=league.id,
            name=league.name,
            code=league.code,
            start_date=league.start_date,
            current_matchday=league.current_matchday,
        )


class LeaguesResponse(BaseModel):
    leagues: list[LeagueResponse]


class MatchResponse(BaseModel):
    id: int
    external_id: str
    league_id: int
    matchday: int
    match_date: datetime
    match_minute: int | None = None
    status: str
    home_team: str
    away_team: str
    use_match: bool
    home_score: int | None = None
    away_score: int | None = None
    game_number: int | None = None

    @classmethod
    def from_domain(cls, match: Match) -> "MatchResponse":
        assert match.id is not None
        return cls(
            id=match.id,
            external_id=match.external_id,
            league_id=match.league_id,
            matchday=match.matchday,
            match_date=match.match_date,
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            game_number=match.game_number,
        )


class MatchesResponse(BaseModel):
    matches: list[MatchResponse]


class AddPredictionRequest(BaseModel):
    game_id: int
    prediction: str


class GetPredictionResponse(BaseModel):
    prediction: str | None


class PredictionResponse(BaseModel):
    id: int
    round_id: int
    auth_id: UUID
    match_id: int
    prediction: str
    dropped: bool = False

    @classmethod
    def from_domain(cls, prediction: Prediction) -> "PredictionResponse":
        assert prediction.id is not None
        return cls(
            id=prediction.id,
            round_id=prediction.round_id,
            auth_id=prediction.auth_id,
            match_id=prediction.match_id,
            prediction=prediction.prediction,
            dropped=prediction.dropped,
        )


class OpenRoundRequest(BaseModel):
    start_date: date
    buy_in_pence: int = Field(default=0, ge=0)


class RoundResponse(BaseModel):
    id: int
    start_date: date
    end_date: date | None = None
    buy_in_pence: int

    @classmethod
    def from_domain(cls, round_: Round) -> "RoundResponse":
        assert round_.id is not None
        return cls(
            id=round_.id,
            start_date=round_.start_date,
            end_date=round_.end_date,
            buy_in_pence=round_.buy_in_pence,
        )


class CurrentRoundResponse(BaseModel):
    current_round: RoundResponse | None
