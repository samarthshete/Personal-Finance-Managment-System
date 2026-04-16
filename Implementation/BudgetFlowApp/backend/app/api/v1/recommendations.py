import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.recommendation import (
    RunRequest, RecommendationRunRead, RecommendationRunListItem, RiskProfileRead,
    WhatIfRequest, WhatIfResponse,
)
from app.services import recommendation_service

router = APIRouter(redirect_slashes=False)


@router.post("/run", status_code=status.HTTP_201_CREATED, response_model=RecommendationRunRead)
async def create_run(
    payload: RunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rp_input = None
    if payload.risk_profile:
        rp_input = {
            "answers": payload.risk_profile.answers.model_dump(),
            "horizon_months": payload.risk_profile.horizon_months,
            "liquidity_need": payload.risk_profile.liquidity_need,
        }

    run = await recommendation_service.execute_run(
        db, current_user.id,
        risk_profile_input=rp_input,
        horizon_override=payload.horizon_months,
        goal_type=payload.goal_type,
        target_horizon_months=payload.target_horizon_months,
        override_contribution_monthly=payload.override_contribution_monthly,
    )
    return run


@router.post("/what-if", response_model=WhatIfResponse)
async def what_if_projection(
    payload: WhatIfRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await recommendation_service.simulate_what_if(
        db,
        current_user.id,
        monthly_amount=payload.monthly_amount,
        goal_type=payload.goal_type,
        target_horizon_months=payload.target_horizon_months,
    )


@router.get("/runs", response_model=List[RecommendationRunListItem])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await recommendation_service.list_runs(db, current_user.id)


@router.get("/runs/{run_id}", response_model=RecommendationRunRead)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await recommendation_service.get_run(db, current_user.id, run_id)


@router.get("/profile", response_model=RiskProfileRead)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from fastapi import HTTPException
    profile = await recommendation_service.get_risk_profile(db, current_user.id)
    if not profile:
        raise HTTPException(status_code=404, detail="No risk profile found. Generate a recommendation first.")
    return profile
