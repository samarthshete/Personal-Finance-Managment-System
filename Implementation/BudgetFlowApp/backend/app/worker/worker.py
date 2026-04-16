"""
UC09 Worker: polls pending jobs, executes handlers.
Run as: python -m app.worker.worker
"""
import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.job import Job
from app.services import job_service
from app.storage.s3_storage import S3Storage
from app.worker.registry import get_handler


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("budgetflow.worker")

POLL_INTERVAL = 0.75
BATCH_SIZE = 5


def _log_event(event: str, **kwargs) -> None:
    payload = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(kwargs)
    logger.info(json.dumps(payload))


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
    jobs = list(result.scalars().unique().all())
    if jobs:
        _log_event("worker.claim_batch", count=len(jobs))
    return jobs


async def _execute_job(session: AsyncSession, job: Job, storage) -> bool:
    """Execute one job. Returns True if a job was processed."""
    job_id_str = str(job.id)
    handler = get_handler(job.type)
    if not handler:
        _log_event("worker.job_failed", job_id=job_id_str, job_type=job.type, reason="unknown_job_type")
        await job_service.mark_failed(
            session,
            job,
            error_message=f"Unknown job type: {job.type}",
        )
        return True

    await job_service.mark_running(session, job)
    _log_event("worker.job_claimed", job_id=job_id_str, job_type=job.type)
    _log_event("worker.job_started", job_id=job_id_str, job_type=job.type)

    try:
        result = await handler(session, job, storage)
        await job_service.mark_succeeded(session, job, result)
        _log_event("worker.job_succeeded", job_id=job_id_str, job_type=job.type)
        return True
    except Exception as exc:
        _log_event("worker.job_failed", job_id=job_id_str, job_type=job.type, error=str(exc)[:500])
        logger.exception("Worker job execution exception")
        await job_service.mark_failed(
            session,
            job,
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
    _log_event("worker.started", poll_interval=POLL_INTERVAL, batch_size=BATCH_SIZE)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                jobs = await _fetch_pending_jobs(session)
                for job in jobs:
                    await _execute_job(session, job, storage)
        except Exception as exc:
            _log_event("worker.loop_error", error=str(exc)[:500])
            logger.exception("Worker polling loop exception")
        await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
