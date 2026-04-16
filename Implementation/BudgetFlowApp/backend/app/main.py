import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import (
    accounts,
    advisor,
    alerts,
    analytics,
    auth,
    budgets,
    categories,
    institutions,
    jobs,
    profile,
    recommendations,
    reports,
    transactions,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("budgetflow.api")

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled API error",
        extra={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["auth"])
app.include_router(profile.router, prefix=f"{settings.API_V1_STR}", tags=["profile"])
app.include_router(accounts.router, prefix=f"{settings.API_V1_STR}/accounts", tags=["accounts"])
app.include_router(institutions.router, prefix=f"{settings.API_V1_STR}/institutions", tags=["institutions"])
app.include_router(transactions.router, prefix=f"{settings.API_V1_STR}/transactions", tags=["transactions"])
app.include_router(categories.router, prefix=f"{settings.API_V1_STR}/categories", tags=["categories"])
app.include_router(budgets.router, prefix=f"{settings.API_V1_STR}/budgets", tags=["budgets"])
app.include_router(alerts.router, prefix=f"{settings.API_V1_STR}/alerts", tags=["alerts"])
app.include_router(analytics.router, prefix=f"{settings.API_V1_STR}/analytics", tags=["analytics"])
app.include_router(reports.router, prefix=f"{settings.API_V1_STR}/reports", tags=["reports"])
app.include_router(jobs.router, prefix=f"{settings.API_V1_STR}/jobs", tags=["jobs"])
app.include_router(advisor.router, prefix=f"{settings.API_V1_STR}/advisor", tags=["advisor"])
app.include_router(recommendations.router, prefix=f"{settings.API_V1_STR}/recommendations", tags=["recommendations"])


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "env": settings.APP_ENV,
    }


def _storage_ready_check() -> tuple[bool, str]:
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            use_ssl=settings.S3_USE_SSL,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.S3_FORCE_PATH_STYLE else "auto"},
            ),
        )
        client.head_bucket(Bucket=settings.S3_BUCKET)
        return True, "ok"
    except Exception as exc:
        logger.warning("Storage readiness check failed: %s", exc)
        return False, str(exc)[:200]


@app.get("/health/ready")
async def readiness_check():
    db_ok = False
    storage_ok = False
    storage_detail = "disabled"

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception as exc:
        logger.warning("DB readiness check failed: %s", exc)

    if settings.S3_BUCKET and settings.S3_ACCESS_KEY and settings.S3_SECRET_KEY:
        storage_ok, storage_detail = await asyncio.to_thread(_storage_ready_check)

    ready = db_ok and (storage_ok or storage_detail == "disabled")
    return {
        "status": "ready" if ready else "not_ready",
        "db": db_ok,
        "storage": {"ok": storage_ok, "detail": storage_detail},
        "app": settings.PROJECT_NAME,
        "env": settings.APP_ENV,
    }
