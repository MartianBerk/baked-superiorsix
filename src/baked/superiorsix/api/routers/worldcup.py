"""World Cup / Qatar Hero routes (legacy subdomains: worldcup, qatarhero)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.deps import (
    RequireAdmin,
    RequireWorldcup,
    get_bearer_token,
    get_worldcup_service,
)
from baked.superiorsix.api.schemas import (
    EndWorldcupTournamentRequest,
    OpenWorldcupTournamentRequest,
    WorldcupAddPredictionRequest,
    WorldcupGetPredictionResponse,
    WorldcupMatchesResponse,
    WorldcupMatchResponse,
    WorldcupMetaResponse,
    WorldcupPredictionResponse,
    WorldcupPredictionsResponse,
    WorldcupScoreResponse,
    WorldcupScoresResponse,
    WorldcupSettingsUpdateRequest,
)
from baked.superiorsix.domain.exceptions import (
    PredictionError,
    WorldcupError,
    WorldcupMatchNotFoundError,
)
from baked.superiorsix.services.worldcup import WorldcupService

router = APIRouter(tags=["worldcup"])


@router.get("/worldcup/meta", response_model=WorldcupMetaResponse)
async def worldcup_meta(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMetaResponse:
    meta = await service.meta()
    return WorldcupMetaResponse(**meta)


@router.get("/worldcup/matches", response_model=WorldcupMatchesResponse)
async def worldcup_matches(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMatchesResponse:
    matches = await service.list_matches()
    return WorldcupMatchesResponse(
        matches=[WorldcupMatchResponse.from_domain(match) for match in matches]
    )


@router.get("/worldcup/scores", response_model=WorldcupScoresResponse)
async def worldcup_scores(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupScoresResponse:
    # ``player`` falls back to auth_id string until SS-29 wires roster names.
    rows = await service.list_scores()
    return WorldcupScoresResponse(scores=[WorldcupScoreResponse.from_domain(row) for row in rows])


@router.get("/worldcup/listpredictions", response_model=WorldcupPredictionsResponse)
async def worldcup_list_predictions(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupPredictionsResponse:
    predictions = await service.list_predictions()
    return WorldcupPredictionsResponse(
        predictions=[WorldcupPredictionResponse.from_domain(p) for p in predictions]
    )


@router.get("/qatarhero/listpredictions", response_model=WorldcupPredictionsResponse)
async def qatarhero_list_predictions(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupPredictionsResponse:
    """Legacy alternate subdomain alias for listpredictions."""
    return await worldcup_list_predictions(service)


@router.get("/worldcup/getpredictions", response_model=WorldcupPredictionsResponse)
async def worldcup_get_predictions(
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupPredictionsResponse:
    return await worldcup_list_predictions(service)


@router.get("/worldcup/getprediction", response_model=WorldcupGetPredictionResponse)
async def worldcup_get_prediction(
    principal: RequireWorldcup,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
    game_id: Annotated[int | None, Query(alias="gameId")] = None,
) -> WorldcupGetPredictionResponse:
    if game_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for gameId.",
        )
    try:
        prediction = await service.get_prediction_for_user(
            auth_id=principal.auth_id,
            match_id=game_id,
        )
    except WorldcupMatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorldcupGetPredictionResponse(
        prediction=(
            None if prediction is None else WorldcupPredictionResponse.from_domain(prediction)
        )
    )


@router.post("/worldcup/addprediction", response_model=WorldcupPredictionResponse)
async def worldcup_add_prediction(
    principal: RequireWorldcup,
    payload: WorldcupAddPredictionRequest,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupPredictionResponse:
    try:
        prediction = await service.submit_prediction(
            auth_id=principal.auth_id,
            match_id=payload.game_id,
            prediction=payload.prediction,
            extra_time=payload.extra_time,
            penalties=payload.penalties,
        )
    except WorldcupMatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PredictionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WorldcupPredictionResponse.from_domain(prediction)


@router.get("/admin/worldcup/settings", response_model=WorldcupMetaResponse)
async def admin_get_worldcup_settings(
    _: RequireAdmin,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMetaResponse:
    meta = await service.meta()
    return WorldcupMetaResponse(**meta)


@router.post("/admin/worldcup/settings", response_model=WorldcupMetaResponse)
async def admin_update_worldcup_settings(
    _: RequireAdmin,
    payload: WorldcupSettingsUpdateRequest,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMetaResponse:
    if payload.buy_in_pence is None and payload.participant_count is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide buy_in_pence and/or participant_count.",
        )
    try:
        await service.update_settings(
            buy_in_pence=payload.buy_in_pence,
            participant_count=payload.participant_count,
        )
    except WorldcupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    meta = await service.meta()
    return WorldcupMetaResponse(**meta)


@router.post("/admin/worldcup/opentournament", response_model=WorldcupMetaResponse)
async def admin_worldcup_open_tournament(
    _: RequireAdmin,
    payload: OpenWorldcupTournamentRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMetaResponse:
    """Open World Cup; grants ``worldcup`` and freezes participant_count."""
    try:
        await service.open_tournament(
            buy_in_pence=payload.buy_in_pence,
            entrant_ids=list(payload.entrant_ids),
            bearer_token=token,
        )
    except WorldcupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    meta = await service.meta()
    return WorldcupMetaResponse(**meta)


@router.post("/admin/worldcup/endtournament", response_model=WorldcupMetaResponse)
async def admin_worldcup_end_tournament(
    _: RequireAdmin,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
    payload: EndWorldcupTournamentRequest | None = None,
) -> WorldcupMetaResponse:
    """Close the open World Cup edition (clears ``open`` for nav)."""
    del payload  # reserved for future end_date parity with euros
    try:
        await service.end_tournament()
    except WorldcupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    meta = await service.meta()
    return WorldcupMetaResponse(**meta)


@router.get("/admin/worldcup/matches", response_model=WorldcupMatchesResponse)
async def admin_list_worldcup_matches(
    _: RequireAdmin,
    service: Annotated[WorldcupService, Depends(get_worldcup_service)],
) -> WorldcupMatchesResponse:
    return await worldcup_matches(service)
