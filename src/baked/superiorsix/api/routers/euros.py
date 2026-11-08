"""Euros routes (legacy path alias: ``/eurowizard/listpredictions``)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from baked.superiorsix.api.deps import RequireEuros, get_euros_service
from baked.superiorsix.api.schemas import (
    EurosAddPredictionRequest,
    EurosGetPredictionResponse,
    EurosMatchesResponse,
    EurosMatchResponse,
    EurosMetaEnvelope,
    EurosMetaResponse,
    EurosPredictionResponse,
    EurosPredictionsResponse,
    EurosScoreResponse,
    EurosScoresResponse,
    EurosTournamentResponse,
)
from baked.superiorsix.domain.exceptions import (
    EurosMatchNotFoundError,
    EurosPredictionError,
)
from baked.superiorsix.services.euros import EurosService

router = APIRouter(tags=["euros"])


def _optional_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    return token or None


@router.get("/euros/meta", response_model=EurosMetaEnvelope)
async def euros_meta(
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosMetaEnvelope:
    """Static prize pot for the open Euros tournament (no round rollover)."""
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
async def euros_matches(
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosMatchesResponse:
    matches = await euros.list_matches()
    return EurosMatchesResponse(
        matches=[EurosMatchResponse.from_domain(match) for match in matches]
    )


@router.get("/euros/scores", response_model=EurosScoresResponse)
async def euros_scores(
    euros: Annotated[EurosService, Depends(get_euros_service)],
    token: Annotated[str | None, Depends(_optional_bearer)] = None,
) -> EurosScoresResponse:
    scores = await euros.list_scores(bearer_token=token)
    return EurosScoresResponse(
        scores=[EurosScoreResponse.from_domain(row) for row in scores]
    )


@router.get("/euros/listpredictions", response_model=EurosPredictionsResponse)
async def euros_list_predictions(
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosPredictionsResponse:
    return await _list_predictions(euros)


@router.get("/eurowizard/listpredictions", response_model=EurosPredictionsResponse)
async def eurowizard_list_predictions(
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosPredictionsResponse:
    """Legacy alternate subdomain alias for listpredictions."""
    return await _list_predictions(euros)


@router.get("/euros/getpredictions", response_model=EurosPredictionsResponse)
async def euros_get_predictions(
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosPredictionsResponse:
    return await _list_predictions(euros)


@router.get("/euros/getprediction")
async def euros_get_prediction(
    principal: RequireEuros,
    euros: Annotated[EurosService, Depends(get_euros_service)],
    game_id: Annotated[int | None, Query(alias="gameId")] = None,
) -> EurosGetPredictionResponse | dict[str, Any]:
    if game_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing mandatory value for gameId.",
        )
    prediction = await euros.get_prediction_for_user(
        auth_id=principal.auth_id,
        match_id=game_id,
    )
    if prediction is None:
        # Legacy clients expect ``{"prediction": null}``.
        return {"prediction": None}
    return EurosGetPredictionResponse(
        prediction=EurosPredictionResponse.from_domain(prediction)
    )


@router.post("/euros/addprediction", response_model=EurosPredictionResponse)
async def euros_add_prediction(
    principal: RequireEuros,
    payload: EurosAddPredictionRequest,
    euros: Annotated[EurosService, Depends(get_euros_service)],
) -> EurosPredictionResponse:
    try:
        prediction = await euros.submit_prediction(
            auth_id=principal.auth_id,
            match_id=payload.game_id,
            prediction=payload.prediction,
            extra_time=payload.extra_time,
            penalties=payload.penalties,
        )
    except EurosMatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EurosPredictionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EurosPredictionResponse.from_domain(prediction)


async def _list_predictions(euros: EurosService) -> EurosPredictionsResponse:
    predictions = await euros.list_predictions()
    return EurosPredictionsResponse(
        predictions=[EurosPredictionResponse.from_domain(item) for item in predictions]
    )
