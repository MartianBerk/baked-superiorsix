from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from baked.superiorsix.domain.models import (
    League,
    Match,
    MatchDetail,
    MatchDifficultyScore,
    Prediction,
    Round,
    TeamResult,
)
from baked.superiorsix.services.subrounds import SubroundStatus


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
    subround_id: int | None = None

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
            subround_id=match.subround_id,
        )


class AvailableMatchesResponse(BaseModel):
    matches: list[AvailableMatchResponse]


class MatchDifficultyPayload(BaseModel):
    """Prediction difficulty: ``score`` in ``[0, 1]`` (higher = harder), ``band`` 1–5."""

    score: float
    band: int = Field(ge=1, le=5)

    @classmethod
    def from_domain(cls, difficulty: MatchDifficultyScore) -> "MatchDifficultyPayload":
        return cls(score=difficulty.score, band=difficulty.band)


class MatchDetailPayload(BaseModel):
    league_position: dict[str, str | None]
    team_performance: dict[str, list[TeamResult]]
    head_to_head: dict[str, list[TeamResult]]
    difficulty: MatchDifficultyPayload | None = None

    @classmethod
    def from_domain(cls, detail: MatchDetail) -> "MatchDetailPayload":
        return cls(
            league_position=detail.league_position,
            team_performance=detail.team_performance,
            head_to_head=detail.head_to_head,
            difficulty=(
                None
                if detail.difficulty is None
                else MatchDifficultyPayload.from_domain(detail.difficulty)
            ),
        )


class MatchDetailResponse(BaseModel):
    match_detail: MatchDetailPayload


class SelectMatchRequest(BaseModel):
    id: int = Field(description="Match id to select for Super Six")
    game_number: int = Field(ge=1, le=6)


class BulkSelectMatchItem(BaseModel):
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
    subround_id: int | None = None
    last_updated: datetime | None = None
    difficulty: MatchDifficultyPayload | None = None

    @classmethod
    def from_domain(
        cls,
        match: Match,
        *,
        difficulty: MatchDifficultyScore | None = None,
    ) -> "MatchResponse":
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
            subround_id=match.subround_id,
            last_updated=match.last_updated,
            difficulty=(
                None if difficulty is None else MatchDifficultyPayload.from_domain(difficulty)
            ),
        )


class MatchesResponse(BaseModel):
    matches: list[MatchResponse]


class AddPredictionRequest(BaseModel):
    game_id: int
    prediction: str


class AdminAddPredictionRequest(BaseModel):
    """Admin submit/update on behalf of a rostered player (``auth_id``)."""

    auth_id: UUID
    match_id: int
    prediction: str


class AdminBulkAddPredictionItem(BaseModel):
    auth_id: UUID
    match_id: int
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


class EndRoundRequest(BaseModel):
    """Close the open round. ``winner_ids`` are roster ``auth_id`` UUIDs (legacy ``player_id``)."""

    winner_ids: list[UUID] = Field(min_length=1)


class RoundResponse(BaseModel):
    id: int
    start_date: date
    end_date: date | None = None
    buy_in_pence: int
    winner_ids: list[UUID] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, round_: Round) -> "RoundResponse":
        assert round_.id is not None
        return cls(
            id=round_.id,
            start_date=round_.start_date,
            end_date=round_.end_date,
            buy_in_pence=round_.buy_in_pence,
            winner_ids=list(round_.winner_ids or []),
        )


class CurrentRoundResponse(BaseModel):
    current_round: RoundResponse | None


class SubroundResponse(BaseModel):
    id: int
    round_id: int
    sequence: int
    created_at: datetime | None = None
    closed_at: datetime | None = None
    match_count: int = 0
    min_kickoff: datetime | None = None
    max_kickoff: datetime | None = None
    is_over: bool = False
    is_open_for_editing: bool = False

    @classmethod
    def from_status(cls, status: SubroundStatus) -> "SubroundResponse":
        subround = status.subround
        assert subround.id is not None
        return cls(
            id=subround.id,
            round_id=subround.round_id,
            sequence=subround.sequence,
            created_at=subround.created_at,
            closed_at=subround.closed_at,
            match_count=status.match_count,
            min_kickoff=status.min_kickoff,
            max_kickoff=status.max_kickoff,
            is_over=status.is_over,
            is_open_for_editing=status.is_open_for_editing,
        )


class SubroundsResponse(BaseModel):
    subrounds: list[SubroundResponse]


class CurrentSubroundResponse(BaseModel):
    current_subround: SubroundResponse | None


class AdminPlayerPickResponse(BaseModel):
    """One active pick for a player on the current slate."""

    id: int
    match_id: int
    game_number: int | None = None
    prediction: str
    home_team: str | None = None
    away_team: str | None = None
    match_date: datetime | None = None


class AdminFlatPredictionResponse(BaseModel):
    """Flat admin prediction row (SS-27 ``listAdminPredictions`` / legacy view)."""

    id: int
    round_id: int
    auth_id: UUID
    match_id: int
    prediction: str
    game_number: int | None = None
    home_team: str | None = None
    away_team: str | None = None
    match_date: datetime | None = None
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None


class AdminPlayerPredictionsResponse(BaseModel):
    """Roster player plus picks for the admin prediction board (SS-17)."""

    auth_id: UUID
    id: UUID
    first_name: str
    last_name: str
    nickname: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    active: bool = True
    prediction_count: int
    match_count: int
    predictions: list[AdminPlayerPickResponse]


class AdminPredictionBoardResponse(BaseModel):
    """Admin prediction payload for a subround or match day.

    ``predictions`` is the flat list SS-27 reads. ``players`` adds
    ``prediction_count`` / slate size for richer admin boards.
    """

    predictions: list[AdminFlatPredictionResponse] = Field(default_factory=list)
    subround: SubroundResponse | None = None
    match_count: int = 0
    matches: list[MatchResponse] = Field(default_factory=list)
    players: list[AdminPlayerPredictionsResponse] = Field(default_factory=list)


class AdminPredictionsListResponse(BaseModel):
    """Flat prediction rows for one player (legacy ``listpredictions`` shape)."""

    predictions: list[AdminPlayerPickResponse]


# Selectable permission chips (invite + roster role edit). `user-profile` is
# always granted. App-scoped ``admin`` is assignable by any superiorsix admin.
INVITE_ALLOWED_ROLES = frozenset({"player", "worldcup", "euros", "admin"})
ALWAYS_GRANT_INVITE_ROLES: tuple[str, ...] = ("user-profile",)
# Tolerate legacy clients that still send always-grant roles in the request body.
_INVITE_ROLE_INPUT_ALLOWED = INVITE_ALLOWED_ROLES | frozenset(ALWAYS_GRANT_INVITE_ROLES)


def with_always_grant_invite_roles(roles: list[str]) -> list[str]:
    """Merge always-granted membership roles (e.g. ``user-profile``) into *roles*."""
    seen = {role.strip().lower() for role in roles if role and role.strip()}
    out = [role for role in roles if role and role.strip()]
    for role in ALWAYS_GRANT_INVITE_ROLES:
        if role not in seen:
            out.append(role)
            seen.add(role)
    return out


def normalize_assignable_roles(value: list[str]) -> list[str]:
    """Validate selectable roles and merge always-granted ones.

    Rejects any non-chip role (legacy ``game-master``, unknown strings, etc.).
    """
    seen: set[str] = set()
    out: list[str] = []
    unknown: list[str] = []
    for role in value:
        normalized = role.strip().lower()
        if not normalized or normalized in seen:
            continue
        if normalized not in _INVITE_ROLE_INPUT_ALLOWED:
            unknown.append(normalized)
            continue
        # Only persist selectable roles from input; always-grant merged below.
        if normalized not in INVITE_ALLOWED_ROLES:
            continue
        seen.add(normalized)
        out.append(normalized)
    if unknown:
        allowed = ", ".join(sorted(INVITE_ALLOWED_ROLES))
        raise ValueError(
            f"Unsupported invite roles: {', '.join(sorted(set(unknown)))}. "
            f"Allowed: {allowed} "
            f"(plus always-granted: {', '.join(ALWAYS_GRANT_INVITE_ROLES)})."
        )
    return with_always_grant_invite_roles(out)


class InvitePlayerRequest(BaseModel):
    """Invite an email onto the Superior Six roster (baked-user invite list)."""

    email: str = Field(min_length=3, max_length=254)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=512)
    roles: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("roles")
    @classmethod
    def normalize_and_validate_roles(cls, value: list[str]) -> list[str]:
        return normalize_assignable_roles(value)


class UpdatePlayerRolesRequest(BaseModel):
    """Replace assignable superiorsix membership roles for a rostered email."""

    email: str = Field(min_length=3, max_length=254)
    roles: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("roles")
    @classmethod
    def normalize_and_validate_roles(cls, value: list[str]) -> list[str]:
        return normalize_assignable_roles(value)


class PlayerRolesResponse(BaseModel):
    email: str
    roles: list[str] = Field(default_factory=list)


class InvitePlayerResponse(BaseModel):
    email: str
    status: str
    auth_id: UUID | None = None
    profile: dict[str, object] = Field(default_factory=dict)
    roles: list[str] = Field(default_factory=list)


class AcceptInviteRequest(BaseModel):
    """Invitee accepts a pending roster invite after set-password claim.

    ``email`` + ``password`` prove identity (password set via baked-auth
    ``POST /v1/auth/set-password``). Membership was granted at invite time;
    this only logs the invitee in and creates the roster profile.
    """

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=512)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AcceptInviteResponse(BaseModel):
    player: "PlayerResponse"
    roles: list[str] = Field(default_factory=list)
    access_token: str
    token_type: str = "bearer"
    audience: str = "superiorsix"


class AddPlayerRequest(BaseModel):
    """Provision a baked-user roster profile for an existing auth subject.

    ``email`` must match a pending invite on baked-user (invite_only roster).
    """

    auth_id: UUID
    email: str = Field(min_length=3, max_length=254)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=512)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
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
