"""
UC09 Worker: polls pending jobs, executes handlers.
Run as: python -m app.worker.worker
"""
import asyncio
import traceback
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.job import Job
from app.services import job_service
from app.worker.registry import get_handler
from app.storage.s3_storage import S3Storage


POLL_INTERVAL = 0.75
BATCH_SIZE = 5


def _get_storage():
    return S3Storage()


async def _fetch_pending_jobs(session: AsyncSession) -> list[Job]:
    stmt = (
        select(Job)
        .where(Job.status == "pending")
        .order_by(Job.created_at.asc())
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


async def _execute_job(session: AsyncSession, job: Job, storage) -> bool:
    """Execute one job. Returns True if a job was processed."""
    handler = get_handler(job.type)
    if not handler:
        await job_service.mark_failed(
            session, job,
            error_message=f"Unknown job type: {job.type}",
        )
        return True

    await job_service.mark_running(session, job)

    try:
        result = await handler(session, job, storage)
        await job_service.mark_succeeded(session, job, result)
        return True
    except Exception as exc:
        await job_service.mark_failed(
            session, job,
            error_message=str(exc)[:2000],
            error_trace=traceback.format_exc(),
        )
        return True


async def run_once(
    storage=None,
    session_factory: Optional[Any] = None,
) -> bool:
    """
    Process one batch of pending jobs. Returns True if any job was processed.
    Used by tests to execute jobs synchronously.

    *session_factory* lets tests use the same AsyncEngine / pool as the FastAPI
    dependency override while still opening a fresh AsyncSession here (required
    so job claim + handler run are not on the API's long-lived test session).
    """
    storage = storage or _get_storage()
    factory: Any = session_factory or AsyncSessionLocal
    async with factory() as session:
        jobs = await _fetch_pending_jobs(session)
        if not jobs:
            return False
        for job in jobs:
            await _execute_job(session, job, storage)
        return True


async def run_loop() -> None:
    """Infinite poll loop for worker process."""
    storage = _get_storage()
    print("Worker started, polling for jobs...")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                jobs = await _fetch_pending_jobs(session)
                for job in jobs:
                    await _execute_job(session, job, storage)
                    print(f"Job {job.id} ({job.type}) -> {job.status}")
        except Exception as exc:
            print(f"Worker error: {exc}")
        await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
