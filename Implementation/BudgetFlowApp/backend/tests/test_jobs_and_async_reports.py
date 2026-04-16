"""Tests for UC09 async job processing and report generation."""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from app.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core import security
from app.models.user import User
from app.api.v1.reports import get_storage
from app.storage.memory_storage import MemoryStorage
from app.worker.worker import run_once

fake_storage = MemoryStorage()


def _override_storage():
    return fake_storage


@pytest_asyncio.fixture(scope="function")
async def db():
    engine = create_async_engine(settings.effective_database_url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _create_user(db: AsyncSession, email: str, name: str = "Test") -> User:
    user = User(email=email, name=name, hashed_password=security.get_password_hash("TestPass1!"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _auth_header(user_id) -> dict:
    token = security.create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = _override_storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _run_worker_until_done(db: AsyncSession, max_iterations: int = 10) -> None:
    """Run worker until no more pending jobs or max iterations."""
    factory = async_sessionmaker(
        bind=db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    for _ in range(max_iterations):
        processed = await run_once(fake_storage, session_factory=factory)
        if not processed:
            break
    db.expire_all()


@pytest.mark.asyncio
async def test_create_report_returns_202_and_job_id(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"async_report_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "csv",
    }, headers=headers)

    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    assert data["job_id"] is not None
    assert data["id"] is not None
    assert data["job_status"] == "pending"


@pytest.mark.asyncio
async def test_worker_executes_report_job(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"worker_report_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "csv",
    }, headers=headers)
    assert res.status_code == 202
    report_id = res.json()["id"]

    await _run_worker_until_done(db)

    get_res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert get_res.status_code == 200
    report = get_res.json()
    assert report["status"] == "succeeded"
    assert report["download_url"] is not None


@pytest.mark.asyncio
async def test_download_works_after_succeeded(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"download_report_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "transactions",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "pdf",
    }, headers=headers)
    assert res.status_code == 202
    report_id = res.json()["id"]

    dl_res = await client.get(f"/api/v1/reports/{report_id}/download", headers=headers)
    assert dl_res.status_code == 409

    await _run_worker_until_done(db)

    dl_res = await client.get(f"/api/v1/reports/{report_id}/download", headers=headers)
    assert dl_res.status_code == 200
    assert "download_url" in dl_res.json()


@pytest.mark.asyncio
async def test_user_isolation_job_and_report(client: AsyncClient, db: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    user_a = await _create_user(db, f"isolation_job_a_{suffix}@test.com", "User A")
    user_b = await _create_user(db, f"isolation_job_b_{suffix}@test.com", "User B")

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "csv",
    }, headers=_auth_header(user_a.id))
    assert res.status_code == 202
    report_id = res.json()["id"]
    job_id = res.json()["job_id"]

    res_b = await client.get(f"/api/v1/reports/{report_id}", headers=_auth_header(user_b.id))
    assert res_b.status_code == 404

    res_b_dl = await client.get(f"/api/v1/reports/{report_id}/download", headers=_auth_header(user_b.id))
    assert res_b_dl.status_code == 404


@pytest.mark.asyncio
async def test_worker_drain_twice_same_test_session_stable(client: AsyncClient, db: AsyncSession):
    """Regression: draining the worker twice must not corrupt asyncpg / engine state."""
    user = await _create_user(db, f"worker_twice_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)
    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "csv",
    }, headers=headers)
    assert res.status_code == 202
    report_id = res.json()["id"]
    await _run_worker_until_done(db)
    r1 = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["status"] == "succeeded"
    await _run_worker_until_done(db)
    r2 = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_idempotency_two_jobs_for_same_request(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"idempotency_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    payload = {"type": "monthly_summary", "from_date": "2026-01-01", "to_date": "2026-01-31", "format": "csv"}
    res1 = await client.post("/api/v1/reports", json=payload, headers=headers)
    res2 = await client.post("/api/v1/reports", json=payload, headers=headers)

    assert res1.status_code == 202
    assert res2.status_code == 202
    assert res1.json()["id"] != res2.json()["id"]
    assert res1.json()["job_id"] != res2.json()["job_id"]

    await _run_worker_until_done(db)

    for report_id in [res1.json()["id"], res2.json()["id"]]:
        get_res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "succeeded"
