"""Authenticated player routes (legacy subdomain: user)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from baked.superiorsix.api.deps import RequireDetails, RequirePlayer, get_prediction_service
from baked.superiorsix.api.schemas import (
    AddPredictionRequest,
    GetPredictionResponse,
    PredictionResponse,
)
from baked.superiorsix.domain.exceptions import MatchNotFoundError, PredictionError
from baked.superiorsix.noop import noop
from baked.superiorsix.services.predictions import PredictionService

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/getprediction", response_model=GetPredictionResponse)
async def get_prediction(
    principal: RequirePlayer,
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
    principal: RequirePlayer,
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


@router.post("/updatedetails")
async def update_details(_: RequireDetails) -> dict[str, Any]:
    return noop()
