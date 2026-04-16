"""
Job type -> handler registry. Handlers receive (db, job, storage).
"""
import base64
import uuid
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.import_session import ImportSession
from app.models.job import Job
from app.services import import_service
from app.storage import ReportStorage


Handler = Callable[[AsyncSession, Job, ReportStorage], Awaitable[dict]]


async def _handle_report_generate(
    db: AsyncSession,
    job: Job,
    storage: ReportStorage,
) -> dict:
    report_id = job.payload.get("report_id")
    if not report_id:
        raise ValueError("report_id required in payload")
    from app.services import report_service
    return await report_service.generate_report_by_id(db, uuid.UUID(report_id), storage)


async def _handle_transactions_import_csv(
    db: AsyncSession,
    job: Job,
    storage: ReportStorage,
) -> dict:
    del storage  # not needed for import jobs
    payload = job.payload or {}
    user_id_raw = payload.get("user_id")
    account_id_raw = payload.get("account_id")
    session_id_raw = payload.get("import_session_id")
    file_base64 = payload.get("file_base64")

    if not user_id_raw or not account_id_raw or not session_id_raw or not file_base64:
        raise ValueError("transactions.import_csv payload is missing required fields")

    user_id = uuid.UUID(str(user_id_raw))
    account_id = uuid.UUID(str(account_id_raw))
    session_id = uuid.UUID(str(session_id_raw))

    result = await db.execute(
        select(ImportSession).where(
            ImportSession.id == session_id,
            ImportSession.user_id == user_id,
            ImportSession.account_id == account_id,
        )
    )
    session = result.scalars().first()
    if not session:
        raise ValueError("Import session not found for job payload")

    try:
        file_bytes = base64.b64decode(file_base64)
    except Exception as exc:
        raise ValueError("Invalid base64 payload for import file") from exc

    try:
        processed_session, row_errors = await import_service.process_import_file(db, session, file_bytes)
        return {
            "import_session_id": str(processed_session.id),
            "inserted_count": processed_session.imported_count,
            "skipped_duplicates": processed_session.duplicate_count,
            "failed_rows": processed_session.failed_count,
            "row_errors": row_errors[:50],
        }
    except Exception as exc:
        session.status = "failed"
        session.completed_at = datetime.now(timezone.utc)
        session.metadata_json = {
            "row_errors": [{"row": 0, "message": str(exc)}],
        }
        await db.commit()
        raise


JOB_HANDLERS: dict[str, Handler] = {
    "report.generate": _handle_report_generate,
    "transactions.import_csv": _handle_transactions_import_csv,
}


def get_handler(job_type: str) -> Optional[Handler]:
    return JOB_HANDLERS.get(job_type)
