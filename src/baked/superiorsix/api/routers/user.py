"""Authenticated player routes (legacy subdomain: user)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from baked.superiorsix.api.deps import (
    RequireActivePlayer,
    get_auth_admin,
    get_bearer_token,
    get_prediction_service,
    get_user_profiles,
)
from baked.superiorsix.api.schemas import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    AddPredictionRequest,
    GetPredictionResponse,
    PlayerResponse,
    PredictionResponse,
    UpdateDetailsRequest,
    with_always_grant_invite_roles,
)
from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.domain.exceptions import (
    AuthAdminError,
    MatchNotFoundError,
    PredictionError,
    RosterError,
)
from baked.superiorsix.services.predictions import PredictionService

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/getprediction", response_model=GetPredictionResponse)
async def get_prediction(
    principal: RequireActivePlayer,
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
    game_id: Annotated[int | None, Query(alias="gameId")] = None,
) -> GetPredictionResponse:
    if game_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for gameId.",
        )
    prediction = await prediction_service.get_for_user(
        auth_id=principal.auth_id,
        match_id=game_id,
    )
    return GetPredictionResponse(prediction=None if prediction is None else prediction.prediction)


@router.post("/addprediction", response_model=PredictionResponse)
async def add_prediction(
    principal: RequireActivePlayer,
    payload: AddPredictionRequest,
    prediction_service: Annotated[PredictionService, Depends(get_prediction_service)],
) -> PredictionResponse:
    try:
        prediction = await prediction_service.submit(
            auth_id=principal.auth_id,
            match_id=payload.game_id,
            prediction=payload.prediction,
        )
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PredictionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PredictionResponse.from_domain(prediction)


@router.post("/updatedetails", response_model=PlayerResponse)
async def update_details(
    _: RequireActivePlayer,
    payload: UpdateDetailsRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
) -> PlayerResponse:
    patch: dict[str, Any] = payload.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Provide at least one of nickname, email, avatar_url, "
                "first_name, last_name."
            ),
        )
    try:
        player = await profiles.patch_me(token, patch)
    except RosterError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PlayerResponse.from_roster(player.to_player_dict())


@router.post(
    "/acceptinvite",
    response_model=AcceptInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invite(
    request: Request,
    payload: AcceptInviteRequest,
    profiles: Annotated[UserProfileGateway, Depends(get_user_profiles)],
    auth_admin: Annotated[AuthAdminGateway, Depends(get_auth_admin)],
) -> AcceptInviteResponse:
    """Accept a pending roster invite after set-password claim.

    Membership roles are granted at invite time. This path only:
    1. Logs in as the invitee (``audience=superiorsix``) with the claimed password.
    2. Creates the roster profile with that JWT (consumes the invite).

    No service bootstrap / auth-admin password is used here.
    """
    settings = request.app.state.settings

    try:
        invitee_token = await auth_admin.login_user(
            email=payload.email,
            password=payload.password,
            audience=settings.jwt_audience,
        )
    except AuthAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    profile_body: dict[str, Any] = {
        key: value
        for key, value in payload.model_dump(exclude_none=True).items()
        if key not in {"email", "password"}
    }
    profile_body.setdefault("email", payload.email)
    try:
        player, consumed_roles = await profiles.create_me(invitee_token, profile_body)
    except RosterError as exc:
        detail = str(exc).lower()
        if "invite" in detail:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if "already exists" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    roles = with_always_grant_invite_roles(list(consumed_roles or []))

    return AcceptInviteResponse(
        player=PlayerResponse.from_roster(player.to_player_dict()),
        roles=roles,
        access_token=invitee_token,
        audience=settings.jwt_audience,
    )
