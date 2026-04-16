"""
UC09 Job service: enqueue, get, list, status transitions.
All operations are user-scoped.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job


async def create_job_in_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    job_type: str,
    payload: dict,
) -> Job:
    """Create job in session and flush. Caller must commit."""
    job = Job(user_id=user_id, type=job_type, payload=payload or {}, status="pending")
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def enqueue_job(
    db: AsyncSession,
    user_id: uuid.UUID,
    job_type: str,
    payload: dict,
) -> Job:
    job = await create_job_in_session(db, user_id, job_type, payload)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job(db: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user_id)
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


async def get_job_owned(db: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    return await get_job(db, user_id, job_id)


async def list_jobs(
    db: AsyncSession,
    user_id: uuid.UUID,
    status_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 50,
) -> List[Job]:
    stmt = (
        select(Job)
        .where(Job.user_id == user_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    if type_filter:
        stmt = stmt.where(Job.type == type_filter)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def mark_running(db: AsyncSession, job: Job) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)


async def mark_succeeded(db: AsyncSession, job: Job, result: Optional[dict] = None) -> None:
    job.status = "succeeded"
    job.result = result
    job.finished_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    job.error_message = None
    job.error_trace = None
    await db.commit()
    await db.refresh(job)


async def mark_failed(
    db: AsyncSession,
    job: Job,
    error_message: str,
    error_trace: Optional[str] = None,
) -> None:
    job.status = "failed"
    job.error_message = (error_message or "")[:2000]
    job.error_trace = (error_trace or "")[:5000] if error_trace else None
    job.finished_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
