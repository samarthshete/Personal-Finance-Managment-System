import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.job import JobRead
from app.services import job_service

router = APIRouter(redirect_slashes=False)


@router.get("", response_model=list[JobRead])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    job_type: Optional[str] = Query(None, alias="type", description="Filter by job type"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    jobs = await job_service.list_jobs(
        db, current_user.id,
        status_filter=status,
        type_filter=job_type,
        limit=limit,
    )
    return [JobRead.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await job_service.get_job_owned(db, current_user.id, job_id)
    return JobRead.model_validate(job)
