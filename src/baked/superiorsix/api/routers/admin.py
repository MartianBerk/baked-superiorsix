"""Admin routes (legacy subdomain: admin).

Match-day / round / roster ops require superiorsix ``admin``.
Roster display resolves via baked-user by auth ``sub`` (no local permissions model).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import (
    RequireAdmin,
    get_auth_admin,
    get_bearer_token,
    get_euros_service,
    get_league_service,
    get_match_service,
    get_prediction_service,
    get_round_service,
    get_stats_service,
    get_subround_service,
    get_user_profiles,
)
from baked.superiorsix.api.schemas import (
    AddPlayerRequest,
    AdminAddPredictionRequest,
    AdminBulkAddPredictionItem,
    AdminFlatPredictionResponse,
    AdminPlayerPickResponse,
    AdminPlayerPredictionsResponse,
    AdminPredictionBoardResponse,
    AdminPredictionsListResponse,
    BulkSelectMatchItem,
    CurrentRoundResponse,
    CurrentSubroundResponse,
    EndEurosTournamentRequest,
    EndRoundRequest,
    EurosMatchesResponse,
    EurosMatchResponse,
    EurosMetaEnvelope,
    EurosMetaResponse,
    EurosTournamentResponse,
    HistoricRoundResponse,
    HistoricRoundsResponse,
    InvitePlayerRequest,
    InvitePlayerResponse,
    LeagueResponse,
    LeaguesResponse,
    MatchesResponse,
    MatchResponse,
    OpenEurosTournamentRequest,
    OpenRoundRequest,
    PlayerResponse,
    PlayerRolesResponse,
    PlayersResponse,
    PredictionResponse,
    RoundResponse,
    SelectMatchRequest,
    SubroundResponse,
    SubroundsResponse,
    UpdatePlayerRolesRequest,
    with_always_grant_invite_roles,
)
from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.domain.exceptions import (
    AuthAdminError,
    EurosTournamentError,
    EurosTournamentNotFoundError,
    MatchNotFoundError,
    MatchSelectionError,
    PredictionError,
    RosterError,
    RosterNotFoundError,
    RoundError,
    RoundNotFoundError,
    SubroundError,
    SubroundNotFoundError,
)
from baked.superiorsix.domain.models import Match, Prediction
from baked.superiorsix.noop import noop
from baked.superiorsix.services.euros import EurosService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService, day_bounds
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.stats import StatsService
from baked.superiorsix.services.subrounds import SubroundService, SubroundStatus

router = APIRouter(prefix="/admin", tags=["admin"])


def _roster_http_error(exc: RosterError) -> HTTPException:
    if isinstance(exc, RosterNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    detail = str(exc).lower()
    if "invite" in detail:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def _pick_from_prediction(
    prediction: Prediction,
    matches_by_id: dict[int, Match],
) -> AdminPlayerPickResponse:
    assert prediction.id is not None
    match = matches_by_id.get(prediction.match_id)
    return AdminPlayerPickResponse(
        id=prediction.id,
        match_id=prediction.match_id,
        game_number=None if match is None else match.game_number,
        prediction=prediction.prediction,
        home_team=None if match is None else match.home_team,
        away_team=None if match is None else match.away_team,
        match_date=None if match is None else match.match_date,
    )


def _flat_from_prediction(
    prediction: Prediction,
    matches_by_id: dict[int, Match],
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    nickname: str | None = None,
) -> AdminFlatPredictionResponse:
    assert prediction.id is not None
    match = matches_by_id.get(prediction.match_id)
    return AdminFlatPredictionResponse(
        id=prediction.id,
        round_id=prediction.round_id,
        auth_id=prediction.auth_id,
        match_id=prediction.match_id,
        prediction=prediction.prediction,
        game_number=None if match is None else match.game_number,
        home_team=None if match is None else match.home_team,
        away_team=None if match is None else match.away_team,
        match_date=None if match is None else match.match_date,
        first_name=first_name,
        last_name=last_name,
        nickname=nickname,
    )


def _build_admin_board(
    *,
    status_: SubroundStatus | None,
    matches: list[Match],
    predictions: list[Prediction],
    roster: list,
) -> AdminPredictionBoardResponse:
    match_count = len(matches)
    matches_by_id = {match.id: match for match in matches if match.id is not None}
    profiles_by_auth = {profile.auth_id: profile for profile in roster}

    flat: list[AdminFlatPredictionResponse] = []
    by_auth: dict[UUID, list[Prediction]] = {}
    for prediction in predictions:
        profile = profiles_by_auth.get(prediction.auth_id)
        flat.append(
            _flat_from_prediction(
                prediction,
                matches_by_id,
                first_name=None if profile is None else profile.first_name,
                last_name=None if profile is None else profile.last_name,
                nickname=None if profile is None else profile.nickname,
            )
        )
        by_auth.setdefault(prediction.auth_id, []).append(prediction)
    flat.sort(
        key=lambda row: (
            (row.last_name or "").lower(),
            (row.first_name or "").lower(),
            row.game_number is None,
            row.game_number or 0,
            row.match_id,
        )
    )

    players: list[AdminPlayerPredictionsResponse] = []
    for profile in sorted(
        roster,
        key=lambda item: (
            (item.last_name or "").lower(),
            (item.first_name or "").lower(),
            str(item.auth_id),
        ),
    ):
        picks = [
            _pick_from_prediction(prediction, matches_by_id)
            for prediction in by_auth.get(profile.auth_id, [])
        ]
        picks.sort(
            key=lambda pick: (pick.game_number is None, pick.game_number or 0, pick.match_id)
        )
        players.append(
            AdminPlayerPredictionsResponse(
                auth_id=profile.auth_id,
                id=profile.id,
                first_name=profile.first_name,
                last_name=profile.last_name,
                nickname=profile.nickname,
                email=profile.email,
                avatar_url=profile.avatar_url,
                active=profile.active,
                prediction_count=len(picks),
                match_count=match_count,
                predictions=picks,
            )
        )

    return AdminPredictionBoardResponse(
        predictions=flat,
        subround=None if status_ is None else SubroundResponse.from_status(status_),
        match_count=match_count,
        matches=[MatchResponse.from_domain(match) for match in matches],
        players=players,
    )


def _sorted_slate_matches(matches: list[Match]) -> list[Match]:
    return sorted(
        matches,
        key=lambda match: (
            match.game_number is None,
            match.game_number or 0,
            match.match_date,
        ),
    )


async def _resolve_admin_slate(
    *,
    subround_service: SubroundService,
    match_service: MatchService,
    subround_id: int | None,
) -> tuple[SubroundStatus | None, list[Match]]:
    if subround_id is not None:
        try:
            subround = await subround_service.get(subround_id)
            statuses = await subround_service.list_for_round(subround.round_id)
        except SubroundNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (RoundNotFoundError, SubroundError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        status_ = next((item for item in statuses if item.subround.id == subround_id), None)
        if status_ is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subround {subround_id} not found.",
            )
        matches = await match_service.selected_matches_for_subround(subround_id)
        return status_, _sorted_slate_matches(matches)

    status_ = await subround_service.current_open_for_editing()
    if status_ is None or status_.subround.id is None:
        return None, []
    matches = await match_service.selected_matches_for_subround(status_.subround.id)
    return status_, _sorted_slate_matches(matches)


@router.get("/listleagues", response_model=LeaguesResponse)
async def list_leagues(
    _: RequireAdmin,
    league_service: Annotated[LeagueService, Depends(get_league_service)],
) -> LeaguesResponse:
    leagues = await league_service.list()
    return LeaguesResponse(leagues=[LeagueResponse.from_domain(league) for league in leagues])


@router.get("/listplayers", response_model=PlayersResponse)
async def list_players(
    _: RequireAdmin,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    active: Annotated[bool | None, Query()] = None,
) -> PlayersResponse:
    try:
        roster = await profiles.list_profiles(token, active=active)
    except RosterError as exc:
        raise _roster_http_error(exc) from exc
    return PlayersResponse(
        players=[PlayerResponse.from_roster(item.to_player_dict()) for item in roster]
    )


@router.get("/listrounds")
async def list_rounds(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> dict[str, list[dict[str, Any]]]:
    rounds = await round_service.list()
    return {
        "rounds": [RoundResponse.from_domain(round_).model_dump(mode="json") for round_ in rounds]
    }


@router.get("/currentround", response_model=CurrentRoundResponse)
async def current_round(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> CurrentRoundResponse:
    round_ = await round_service.current_or_none()
    return CurrentRoundResponse(
        current_round=None if round_ is None else RoundResponse.from_domain(round_)
    )


@router.get("/historicrounds", response_model=HistoricRoundsResponse)
async def historic_rounds(
    _: RequireAdmin,
    token: Annotated[str, Depends(get_bearer_token)],
    stats: Annotated[StatsService, Depends(get_stats_service)],
) -> HistoricRoundsResponse:
    """Full multi-round winners archive (same payload as ``GET /stats/winners``)."""
    rounds = await stats.historic_rounds(bearer_token=token)
    return HistoricRoundsResponse(
        rounds=[HistoricRoundResponse.from_domain(row) for row in rounds]
    )


@router.get("/listsubrounds", response_model=SubroundsResponse)
async def list_subrounds(
    _: RequireAdmin,
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    round_id: Annotated[int | None, Query(alias="roundId")] = None,
) -> SubroundsResponse:
    """List subrounds for the current (or given) round.

    Read-only: never creates Round or Subround. No open Round → ``{subrounds: []}``.
    """
    try:
        if round_id is None:
            statuses = await subround_service.list_for_current_round()
        else:
            statuses = await subround_service.list_for_round(round_id)
    except (RoundNotFoundError, SubroundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SubroundsResponse(
        subrounds=[SubroundResponse.from_status(item) for item in statuses]
    )


@router.get("/currentsubround", response_model=CurrentSubroundResponse)
async def current_subround(
    _: RequireAdmin,
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    create: Annotated[bool, Query()] = False,
) -> CurrentSubroundResponse:
    """Return the open-for-editing subround.

    Default is read-only (``null`` with no round / no open subround). Pass
    ``create=true`` to open the next sequence when none exist or the latest is
    over — **400** ``No round in progress.`` if no parent Round. The usual first
    subround is created on ``addmatches`` submit instead.
    """
    try:
        if create:
            status_ = await subround_service.current_or_create_open()
        else:
            status_ = await subround_service.current_open_for_editing()
    except (RoundNotFoundError, SubroundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CurrentSubroundResponse(
        current_subround=None if status_ is None else SubroundResponse.from_status(status_)
    )


@router.post("/opensubround", response_model=SubroundResponse)
async def open_subround(
    _: RequireAdmin,
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
) -> SubroundResponse:
    """Force-create the next subround for the current round (editing target).

    Requires an open parent Round — 400 ``No round in progress.`` otherwise.
    """
    try:
        status_ = await subround_service.open_next(force=True)
    except (RoundNotFoundError, SubroundError, SubroundNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SubroundResponse.from_status(status_)


@router.get("/listpredictions", response_model=AdminPredictionsListResponse)
async def list_predictions(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    auth_id: Annotated[UUID | None, Query(alias="authId")] = None,
    subround_id: Annotated[int | None, Query(alias="subroundId")] = None,
) -> AdminPredictionsListResponse:
    """Flat picks for one player on the current (or given) subround slate.

    Query ``authId`` is required (legacy ``playerid`` → roster ``auth_id``).
    """
    if auth_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for authId.",
        )

    status_, matches = await _resolve_admin_slate(
        subround_service=subround_service,
        match_service=match_service,
        subround_id=subround_id,
    )
    if status_ is None:
        return AdminPredictionsListResponse(predictions=[])
    match_ids = [match.id for match in matches if match.id is not None]
    if not match_ids:
        return AdminPredictionsListResponse(predictions=[])

    matches_by_id = {match.id: match for match in matches if match.id is not None}
    predictions = await prediction_service.list_active_for_matches(
        round_id=status_.subround.round_id,
        match_ids=match_ids,
    )
    picks = [
        _pick_from_prediction(prediction, matches_by_id)
        for prediction in predictions
        if prediction.auth_id == auth_id
    ]
    picks.sort(key=lambda pick: (pick.game_number is None, pick.game_number or 0, pick.match_id))
    return AdminPredictionsListResponse(predictions=picks)


@router.get("/listpredictionsnew", response_model=AdminPredictionBoardResponse)
async def list_predictions_new(
    _: RequireAdmin,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    match_service: Annotated[MatchService, Depends(get_match_service)],
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
    round_service: Annotated[RoundService, Depends(get_round_service)],
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    subround_id: Annotated[int | None, Query(alias="subroundId")] = None,
    match_date: Annotated[str | None, Query(alias="matchdate")] = None,
) -> AdminPredictionBoardResponse:
    """Predictions for a match day or subround slate.

    SS-27 reads the flat ``predictions`` array (``?matchdate=dd-mm-yyyy``).
    ``players`` includes ``prediction_count`` / slate size for richer boards.
    Without ``matchdate`` / ``subroundId``, uses the open-for-editing subround.
    """
    try:
        roster = await profiles.list_profiles(token, active=True)
    except RosterError as exc:
        raise _roster_http_error(exc) from exc

    if match_date is not None:
        day = parse_legacy_date(match_date, field_name="matchdate")
        matches = _sorted_slate_matches(
            await match_service.list_on_date(day, available_only=True)
        )
        round_ = await round_service.current_or_none()
        if round_ is None or round_.id is None or not matches:
            return _build_admin_board(
                status_=None,
                matches=matches,
                predictions=[],
                roster=roster,
            )
        match_ids = [match.id for match in matches if match.id is not None]
        predictions = await prediction_service.list_active_for_matches(
            round_id=round_.id,
            match_ids=match_ids,
        )
        status_: SubroundStatus | None = None
        subround_ids = {match.subround_id for match in matches if match.subround_id is not None}
        if len(subround_ids) == 1:
            only_id = next(iter(subround_ids))
            status_, _ = await _resolve_admin_slate(
                subround_service=subround_service,
                match_service=match_service,
                subround_id=only_id,
            )
        return _build_admin_board(
            status_=status_,
            matches=matches,
            predictions=predictions,
            roster=roster,
        )

    status_, matches = await _resolve_admin_slate(
        subround_service=subround_service,
        match_service=match_service,
        subround_id=subround_id,
    )
    if status_ is None:
        return _build_admin_board(
            status_=None,
            matches=[],
            predictions=[],
            roster=roster,
        )

    match_ids = [match.id for match in matches if match.id is not None]
    predictions = await prediction_service.list_active_for_matches(
        round_id=status_.subround.round_id,
        match_ids=match_ids,
    )
    return _build_admin_board(
        status_=status_,
        matches=matches,
        predictions=predictions,
        roster=roster,
    )


@router.post("/addprediction", response_model=PredictionResponse)
async def add_prediction(
    _: RequireAdmin,
    payload: AdminAddPredictionRequest,
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
) -> PredictionResponse:
    """Submit or update a Home/Draw/Away pick on behalf of a player.

    Admin bypasses the player 10-minute cutoff (legacy admin behaviour).
    ``prediction`` accepts ``home``/``draw``/``away`` or ``H``/``D``/``A``.
    """
    try:
        prediction = await prediction_service.submit_for_admin(
            auth_id=payload.auth_id,
            match_id=payload.match_id,
            prediction=payload.prediction,
        )
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PredictionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PredictionResponse.from_domain(prediction)


@router.post(
    "/inviteplayer",
    response_model=InvitePlayerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_player(
    request: Request,
    _: RequireAdmin,
    payload: InvitePlayerRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
) -> InvitePlayerResponse:
    """Create passwordless auth user + membership, then roster invite.

    Authorized by the caller's superiorsix ``admin`` bearer (roster invite).
    Auth ``POST /v1/users`` + membership require ``aud=auth`` admin, so the BFF
    uses server-side ``SUPERIORSIX_AUTH_ADMIN_*`` for those calls (SS-66).
    Invitee claims via ``POST /v1/auth/set-password``, then acceptinvite.
    """
    settings = request.app.state.settings
    roles = with_always_grant_invite_roles(list(payload.roles))
    try:
        created = await auth_admin.create_invite_ready_user(
            email=payload.email,
            roles=roles,
            application=settings.jwt_audience,
        )
        if created.memberships:
            roles = with_always_grant_invite_roles(list(created.memberships[0].roles))
    except AuthAdminError as exc:
        detail = str(exc)
        if detail.startswith("EMAIL_EXISTS:"):
            try:
                membership = await auth_admin.set_membership(
                    email=payload.email,
                    roles=roles,
                    application=settings.jwt_audience,
                )
                roles = with_always_grant_invite_roles(list(membership.roles))
            except AuthAdminError as membership_exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(membership_exc),
                ) from membership_exc
        elif "not configured" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Invite auth provisioning is not configured: set "
                    "SUPERIORSIX_AUTH_ADMIN_EMAIL / SUPERIORSIX_AUTH_ADMIN_PASSWORD "
                    "(local-dev may reuse AUTH_BOOTSTRAP_*). Required for aud=auth "
                    "POST /v1/users (SS-66)."
                ),
            ) from exc
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=detail,
            ) from exc

    seed: dict[str, Any] = {"email": payload.email}
    if payload.first_name is not None:
        seed["first_name"] = payload.first_name
    if payload.last_name is not None:
        seed["last_name"] = payload.last_name
    if payload.nickname is not None:
        seed["nickname"] = payload.nickname
    if payload.avatar_url is not None:
        seed["avatar_url"] = payload.avatar_url
    try:
        invite = await profiles.invite(
            token,
            payload.email,
            profile=seed or None,
            roles=roles,
        )
    except RosterError as exc:
        raise _roster_http_error(exc) from exc
    return InvitePlayerResponse(
        email=invite.email,
        status=invite.status,
        auth_id=invite.auth_id,
        profile=dict(invite.profile),
        roles=list(invite.roles) or roles,
    )


@router.get("/getplayerroles", response_model=PlayerRolesResponse)
async def get_player_roles(
    request: Request,
    _: RequireAdmin,
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
    email: Annotated[str, Query(min_length=3, max_length=254)],
) -> PlayerRolesResponse:
    """Read superiorsix membership roles for a rostered email (SS-76)."""
    settings = request.app.state.settings
    normalized = email.strip().lower()
    try:
        membership = await auth_admin.get_membership(
            email=normalized,
            application=settings.jwt_audience,
        )
    except AuthAdminError as exc:
        detail = str(exc)
        if "not configured" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Auth admin credentials are not configured: set "
                    "SUPERIORSIX_AUTH_ADMIN_EMAIL / SUPERIORSIX_AUTH_ADMIN_PASSWORD."
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        ) from exc
    return PlayerRolesResponse(email=normalized, roles=list(membership.roles))


@router.post("/updateroles", response_model=PlayerRolesResponse)
async def update_player_roles(
    request: Request,
    _: RequireAdmin,
    payload: UpdatePlayerRolesRequest,
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
) -> PlayerRolesResponse:
    """Replace assignable membership roles (SS-76 / SS-98).

    Uses server-side ``SUPERIORSIX_AUTH_ADMIN_*`` for baked-auth
    ``PUT /v1/applications/{slug}/memberships/{email}``. App-scoped ``admin``
    is assignable via the same chip set as invite; ``user-profile`` is always
    granted.
    """
    settings = request.app.state.settings
    roles = with_always_grant_invite_roles(list(payload.roles))
    try:
        membership = await auth_admin.set_membership(
            email=payload.email,
            roles=roles,
            application=settings.jwt_audience,
        )
    except AuthAdminError as exc:
        detail = str(exc)
        if "not configured" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Auth admin credentials are not configured: set "
                    "SUPERIORSIX_AUTH_ADMIN_EMAIL / SUPERIORSIX_AUTH_ADMIN_PASSWORD."
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from exc
    return PlayerRolesResponse(
        email=payload.email,
        roles=list(membership.roles),
    )


@router.post("/addplayer", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
async def add_player(
    _: RequireAdmin,
    payload: AddPlayerRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
) -> PlayerResponse:
    """Create roster profile for an auth subject that already has a pending invite."""
    profile_body: dict[str, Any] = {
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "email": payload.email,
    }
    if payload.nickname is not None:
        profile_body["nickname"] = payload.nickname
    if payload.avatar_url is not None:
        profile_body["avatar_url"] = payload.avatar_url
    try:
        player = await profiles.create_for_auth_id(
            token,
            payload.auth_id,
            profile_body,
            email=payload.email,
        )
    except RosterError as exc:
        if "already exists" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        raise _roster_http_error(exc) from exc
    return PlayerResponse.from_roster(player.to_player_dict())


@router.post("/addround", response_model=RoundResponse)
async def add_round(
    _: RequireAdmin,
    payload: OpenRoundRequest,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> RoundResponse:
    try:
        round_ = await round_service.open_round(
            start_date=payload.start_date,
            buy_in_pence=payload.buy_in_pence,
        )
    except RoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoundResponse.from_domain(round_)


@router.post("/endround", response_model=RoundResponse)
async def end_round(
    _: RequireAdmin,
    round_service: Annotated[RoundService, Depends(get_round_service)],
    payload: EndRoundRequest,
    end_date: Annotated[str | None, Query(alias="endDate")] = None,
) -> RoundResponse:
    """Close the open round and record one or more winners.

    Query ``endDate`` (``dd-mm-yyyy``) is required. Body ``winner_ids`` is a
    non-empty list of roster ``auth_id`` UUIDs (same identity as ``listplayers``).
    """
    if not end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing endDate",
        )
    day = parse_legacy_date(end_date, field_name="endDate")
    try:
        round_ = await round_service.end_current(day, payload.winner_ids)
    except RoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoundResponse.from_domain(round_)


@router.get("/getround")
async def get_round(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/listmatches", response_model=MatchesResponse)
async def list_matches(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    from_date: Annotated[str | None, Query(alias="fromDate")] = None,
    to_date: Annotated[str | None, Query(alias="toDate")] = None,
) -> MatchesResponse:
    """List fixtures for an inclusive UTC date range (max 7 calendar days).

    Both ``fromDate`` and ``toDate`` are required (``dd-mm-yyyy``).
    ``fromDate=toDate`` is a single day. Day bounds are UTC midnight-aligned.
    """
    if not from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing fromDate",
        )
    if not to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing toDate",
        )

    start_day = parse_legacy_date(from_date, field_name="fromDate")
    end_day = parse_legacy_date(to_date, field_name="toDate")
    if end_day < start_day:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="toDate must be on or after fromDate",
        )
    span_days = (end_day - start_day).days + 1
    if span_days > 7:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date range must be at most 7 days",
        )

    start, _ = day_bounds(start_day)
    _, end = day_bounds(end_day)
    matches = await match_service.list_in_range(start=start, end=end)
    difficulties = await match_service.difficulties_for_matches(matches)
    return MatchesResponse(
        matches=[
            MatchResponse.from_domain(
                match,
                difficulty=None if match.id is None else difficulties.get(match.id),
            )
            for match in matches
        ]
    )


@router.get("/getmatch", response_model=MatchResponse)
async def get_match(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    match_id: Annotated[int | None, Query(alias="id")] = None,
) -> MatchResponse:
    if match_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing id",
        )
    try:
        match = await match_service.get(match_id)
        difficulty = await match_service.difficulty_for_match(match)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MatchResponse.from_domain(match, difficulty=difficulty)


@router.post("/addmatch", response_model=MatchResponse)
async def add_match(
    _: RequireAdmin,
    payload: SelectMatchRequest,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
) -> MatchResponse:
    try:
        subround = await subround_service.require_open_for_selection()
        assert subround.id is not None
        match = await match_service.select_for_round(
            payload.id,
            payload.game_number,
            subround_id=subround.id,
        )
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (MatchSelectionError, SubroundError, RoundNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MatchResponse.from_domain(match)


@router.post("/addmatches", response_model=MatchesResponse)
async def add_matches(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    payload: Annotated[list[BulkSelectMatchItem], Body()],
    match_date: Annotated[str | None, Query(alias="matchDate")] = None,
) -> MatchesResponse:
    if not match_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing matchDate",
        )
    day = parse_legacy_date(match_date, field_name="matchDate")
    try:
        subround = await subround_service.require_open_for_selection()
        assert subround.id is not None
        matches = await match_service.replace_day_selections(
            day,
            [(item.id, item.game_number) for item in payload],
            subround_id=subround.id,
        )
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (MatchSelectionError, SubroundError, RoundNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MatchesResponse(matches=[MatchResponse.from_domain(match) for match in matches])


@router.get("/dropmatch", response_model=MatchResponse)
async def drop_match(
    _: RequireAdmin,
    match_service: Annotated[MatchService, Depends(get_match_service)],
    match_id: Annotated[int | None, Query(alias="id")] = None,
) -> MatchResponse:
    if match_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing id",
        )
    try:
        match = await match_service.deselect_from_round(match_id)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MatchResponse.from_domain(match)


@router.post("/addpredictions", response_model=dict[str, list[PredictionResponse]])
async def add_predictions(
    _: RequireAdmin,
    payload: Annotated[list[AdminBulkAddPredictionItem], Body()],
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
) -> dict[str, list[PredictionResponse]]:
    """Bulk admin submit (legacy ``addpredictions``). Cutoff is not enforced."""
    created: list[PredictionResponse] = []
    try:
        for item in payload:
            prediction = await prediction_service.submit_for_admin(
                auth_id=item.auth_id,
                match_id=item.match_id,
                prediction=item.prediction,
            )
            created.append(PredictionResponse.from_domain(prediction))
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PredictionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"predictions": created}


@router.get("/dropprediction", response_model=PredictionResponse)
async def drop_prediction(
    _: RequireAdmin,
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
    prediction_id: Annotated[int | None, Query(alias="id")] = None,
) -> PredictionResponse:
    """Soft-drop a prediction so admin can clear a mistaken pick."""
    if prediction_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing id",
        )
    try:
        prediction = await prediction_service.drop(prediction_id)
    except PredictionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PredictionResponse.from_domain(prediction)


@router.post("/setspecialmessage")
async def set_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/getspecialmessage")
async def get_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/endspecialmessage")
async def end_special_message(_: RequireAdmin) -> dict[str, Any]:
    return noop()


@router.get("/dropplayer", response_model=PlayerResponse)
async def drop_player(
    _: RequireAdmin,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_id: Annotated[UUID | None, Query(alias="authId")] = None,
) -> PlayerResponse:
    if auth_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for authId.",
        )
    try:
        player = await profiles.patch_by_auth_id(token, auth_id, {"active": False})
    except RosterError as exc:
        raise _roster_http_error(exc) from exc
    return PlayerResponse.from_roster(player.to_player_dict())


@router.get("/reactivateplayer", response_model=PlayerResponse)
async def reactivate_player(
    _: RequireAdmin,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_id: Annotated[UUID | None, Query(alias="authId")] = None,
) -> PlayerResponse:
    if auth_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for authId.",
        )
    try:
        player = await profiles.patch_by_auth_id(token, auth_id, {"active": True})
    except RosterError as exc:
        raise _roster_http_error(exc) from exc
    return PlayerResponse.from_roster(player.to_player_dict())


# --- Euro Wizard admin (SS-19) ---


@router.get("/euros/meta", response_model=EurosMetaEnvelope)
async def admin_euros_meta(
    _: RequireAdmin,
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosMetaEnvelope:
    meta = await euros.meta()
    tournament = meta.tournament
    return EurosMetaEnvelope(
        meta=EurosMetaResponse(
            open=meta.open,
            buy_in_pence=meta.buy_in_pence,
            entrant_count=meta.entrant_count,
            prize_pot_pence=meta.prize_pot_pence,
            tournament=(
                None if tournament is None else EurosTournamentResponse.from_domain(tournament)
            ),
        )
    )


@router.get("/euros/matches", response_model=EurosMatchesResponse)
async def admin_euros_matches(
    _: RequireAdmin,
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosMatchesResponse:
    matches = await euros.list_matches()
    return EurosMatchesResponse(
        matches=[EurosMatchResponse.from_domain(match) for match in matches]
    )


@router.post("/euros/opentournament", response_model=EurosTournamentResponse)
async def admin_euros_open_tournament(
    _: RequireAdmin,
    payload: OpenEurosTournamentRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosTournamentResponse:
    """Open a Euro Wizard edition; grants ``euros`` and freezes entrant_count."""
    try:
        tournament = await euros.open_tournament(
            start_date=payload.start_date,
            buy_in_pence=payload.buy_in_pence,
            bearer_token=token,
            entrant_ids=list(payload.entrant_ids),
            label=payload.label,
        )
    except EurosTournamentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EurosTournamentResponse.from_domain(tournament)


@router.post("/euros/endtournament", response_model=EurosTournamentResponse)
async def admin_euros_end_tournament(
    _: RequireAdmin,
    euros: Annotated[EurosService, Depends(get_euros_service)],
    payload: EndEurosTournamentRequest | None = None,
) -> EurosTournamentResponse:
    body = payload or EndEurosTournamentRequest()
    try:
        tournament = await euros.end_tournament(end_date=body.end_date)
    except EurosTournamentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return EurosTournamentResponse.from_domain(tournament)
