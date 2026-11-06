"""Admin routes (legacy subdomain: admin).

Match-day / round ops require ``game-master``; user setup requires ``admin``.
Roster display resolves via baked-user by auth ``sub`` (no local permissions model).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from baked.superiorsix.api.dates import parse_legacy_date
from baked.superiorsix.api.deps import (
    RequireAdmin,
    RequireAdminOrGameMaster,
    RequireGameMaster,
    get_auth_admin,
    get_bearer_token,
    get_league_service,
    get_match_service,
    get_round_service,
    get_subround_service,
    get_user_profiles,
)
from baked.superiorsix.api.schemas import (
    AddPlayerRequest,
    BulkSelectMatchItem,
    CurrentRoundResponse,
    CurrentSubroundResponse,
    InvitePlayerRequest,
    InvitePlayerResponse,
    LeagueResponse,
    LeaguesResponse,
    MatchesResponse,
    MatchResponse,
    OpenRoundRequest,
    PlayerResponse,
    PlayersResponse,
    RoundResponse,
    SelectMatchRequest,
    SubroundResponse,
    SubroundsResponse,
    with_always_grant_invite_roles,
)
from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.domain.exceptions import (
    AuthAdminError,
    MatchNotFoundError,
    MatchSelectionError,
    RosterError,
    RosterNotFoundError,
    RoundError,
    RoundNotFoundError,
    SubroundError,
    SubroundNotFoundError,
)
from baked.superiorsix.noop import noop
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService, day_bounds
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.subrounds import SubroundService

router = APIRouter(prefix="/admin", tags=["admin"])


def _roster_http_error(exc: RosterError) -> HTTPException:
    if isinstance(exc, RosterNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    detail = str(exc).lower()
    if "invite" in detail:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/listleagues", response_model=LeaguesResponse)
async def list_leagues(
    _: RequireGameMaster,
    league_service: Annotated[LeagueService, Depends(get_league_service)],
) -> LeaguesResponse:
    leagues = await league_service.list()
    return LeaguesResponse(leagues=[LeagueResponse.from_domain(league) for league in leagues])


@router.get("/listplayers", response_model=PlayersResponse)
async def list_players(
    _: RequireAdminOrGameMaster,
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
    _: RequireGameMaster,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> dict[str, list[dict[str, Any]]]:
    rounds = await round_service.list()
    return {
        "rounds": [RoundResponse.from_domain(round_).model_dump(mode="json") for round_ in rounds]
    }


@router.get("/currentround", response_model=CurrentRoundResponse)
async def current_round(
    _: RequireGameMaster,
    round_service: Annotated[RoundService, Depends(get_round_service)],
) -> CurrentRoundResponse:
    round_ = await round_service.current_or_none()
    return CurrentRoundResponse(
        current_round=None if round_ is None else RoundResponse.from_domain(round_)
    )


@router.get("/historicrounds")
async def historic_rounds(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/listsubrounds", response_model=SubroundsResponse)
async def list_subrounds(
    _: RequireGameMaster,
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    round_id: Annotated[int | None, Query(alias="roundId")] = None,
) -> SubroundsResponse:
    """List subrounds for the current (or given) round.

    With no open parent Round, returns ``{subrounds: []}``. When a round exists
    but has no subrounds yet, opens sequence 1 for the admin scrubber.
    """
    try:
        if round_id is None:
            statuses = await subround_service.list_for_current_round(
                open_first_if_empty=True
            )
        else:
            statuses = await subround_service.list_for_round(round_id)
    except (RoundNotFoundError, SubroundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SubroundsResponse(
        subrounds=[SubroundResponse.from_status(item) for item in statuses]
    )


@router.get("/currentsubround", response_model=CurrentSubroundResponse)
async def current_subround(
    _: RequireGameMaster,
    subround_service: Annotated[SubroundService, Depends(get_subround_service)],
    create: Annotated[bool, Query()] = False,
) -> CurrentSubroundResponse:
    """Return the open-for-editing subround.

    Requires an open parent Round (manual admin creation). With no round,
    ``current_subround`` is ``null``. Pass ``create=true`` to open the next
    sequence when none exist or the latest is over (400 if no round).
    """
    try:
        if create:
            status_ = await subround_service.current_or_create_open()
        else:
            status_ = await subround_service.current_open_for_editing(
                open_first_if_empty=True
            )
    except (RoundNotFoundError, SubroundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CurrentSubroundResponse(
        current_subround=None if status_ is None else SubroundResponse.from_status(status_)
    )


@router.post("/opensubround", response_model=SubroundResponse)
async def open_subround(
    _: RequireGameMaster,
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


@router.get("/listpredictions")
async def list_predictions(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/listpredictionsnew")
async def list_predictions_new(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.post("/addprediction")
async def add_prediction(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


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
    _: RequireGameMaster,
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
    _: RequireGameMaster,
    round_service: Annotated[RoundService, Depends(get_round_service)],
    end_date: Annotated[str | None, Query(alias="endDate")] = None,
) -> RoundResponse:
    if not end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing endDate",
        )
    day = parse_legacy_date(end_date, field_name="endDate")
    try:
        round_ = await round_service.end_current(day)
    except RoundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RoundResponse.from_domain(round_)


@router.get("/getround")
async def get_round(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/listmatches", response_model=MatchesResponse)
async def list_matches(
    _: RequireGameMaster,
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
    _: RequireGameMaster,
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
    _: RequireGameMaster,
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
    _: RequireGameMaster,
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
    _: RequireGameMaster,
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


@router.post("/addpredictions")
async def add_predictions(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/dropprediction")
async def drop_prediction(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.post("/setspecialmessage")
async def set_special_message(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/getspecialmessage")
async def get_special_message(_: RequireGameMaster) -> dict[str, Any]:
    return noop()


@router.get("/endspecialmessage")
async def end_special_message(_: RequireGameMaster) -> dict[str, Any]:
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
