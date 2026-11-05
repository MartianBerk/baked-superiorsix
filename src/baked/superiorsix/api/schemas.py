from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @classmethod
    def from_domain(cls, league: League) -> "LeagueResponse":
        assert league.id is not None
        return cls(
            id=league.id,
            name=league.name,
            code=league.code,
            start_date=league.start_date,
        )


class LeaguesResponse(BaseModel):
    leagues: list[LeagueResponse]


class MatchResponse(BaseModel):
    id: int
    external_id: str
    league_id: int
    match_date: datetime
    match_minute: int | None = None
    status: str
    home_team: str
    away_team: str
    use_match: bool
    home_score: int | None = None
    away_score: int | None = None
    game_number: int | None = None
    last_updated: datetime | None = None

    @classmethod
    def from_domain(cls, match: Match) -> "MatchResponse":
        assert match.id is not None
        return cls(
            id=match.id,
            external_id=match.external_id,
            league_id=match.league_id,
            match_date=match.match_date,
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            game_number=match.game_number,
            last_updated=match.last_updated,
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


INVITE_ALLOWED_ROLES = frozenset({"player", "user-profile", "worldcup", "euros"})


class InvitePlayerRequest(BaseModel):
    """Invite a username onto the Superior Six roster (baked-user invite list)."""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    avatar_url: str | None = Field(default=None, max_length=512)
    roles: list[str] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("roles")
    @classmethod
    def normalize_and_validate_roles(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        unknown: list[str] = []
        for role in value:
            normalized = role.strip().lower()
            if not normalized or normalized in seen:
                continue
            if normalized not in INVITE_ALLOWED_ROLES:
                unknown.append(normalized)
                continue
            seen.add(normalized)
            out.append(normalized)
        if unknown:
            allowed = ", ".join(sorted(INVITE_ALLOWED_ROLES))
            raise ValueError(
                f"Unsupported invite roles: {', '.join(sorted(set(unknown)))}. "
                f"Allowed: {allowed}."
            )
        return out


class InvitePlayerResponse(BaseModel):
    username: str
    status: str
    auth_id: UUID | None = None
    profile: dict[str, object] = Field(default_factory=dict)
    roles: list[str] = Field(default_factory=list)


class AcceptInviteRequest(BaseModel):
    """Invitee accepts a pending roster invite after registering their password.

    ``username`` + ``password`` prove identity (password chosen at register).
    The BFF grants invite.roles via auth-admin, obtains the invitee's
    superiorsix JWT, then creates the roster profile with that JWT.
    """

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    avatar_url: str | None = Field(default=None, max_length=512)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class AcceptInviteResponse(BaseModel):
    player: "PlayerResponse"
    roles: list[str] = Field(default_factory=list)
    access_token: str
    token_type: str = "bearer"
    audience: str = "superiorsix"


class AddPlayerRequest(BaseModel):
    """Provision a baked-user roster profile for an existing auth subject.

    ``username`` must match a pending invite on baked-user (invite_only roster).
    """

    auth_id: UUID
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=512)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class UpdateDetailsRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    avatar_url: str | None = Field(default=None, max_length=512)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)


class PlayerResponse(BaseModel):
    auth_id: UUID
    id: UUID
    first_name: str
    last_name: str
    nickname: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    active: bool = True
    retired: bool = False

    @classmethod
    def from_roster(cls, player: dict[str, object]) -> "PlayerResponse":
        return cls.model_validate(player)


class PlayersResponse(BaseModel):
    players: list[PlayerResponse]
