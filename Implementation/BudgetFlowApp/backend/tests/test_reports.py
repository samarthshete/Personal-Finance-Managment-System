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


async def _run_worker_until_done(db: AsyncSession, max_iterations: int = 10) -> None:
    await db.rollback()
    factory = async_sessionmaker(
        bind=db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    for _ in range(max_iterations):
        if not await run_once(fake_storage, session_factory=factory):
            break
    db.expire_all()


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


@pytest.mark.asyncio
async def test_create_report_csv(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"report_csv_{uuid.uuid4().hex[:8]}@test.com")
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
    assert data["type"] == "monthly_summary"
    assert data["format"] == "csv"
    await _run_worker_until_done(db)
    get_res = await client.get(f"/api/v1/reports/{data['id']}", headers=headers)
    assert get_res.status_code == 200
    final = get_res.json()
    assert final["status"] == "succeeded"
    assert final["download_url"] is not None


@pytest.mark.asyncio
async def test_create_report_pdf(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"report_pdf_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "transactions",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "pdf",
    }, headers=headers)

    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "queued"
    await _run_worker_until_done(db)
    get_res = await client.get(f"/api/v1/reports/{data['id']}", headers=headers)
    assert get_res.status_code == 200
    final = get_res.json()
    assert final["status"] == "succeeded"
    assert final["format"] == "pdf"
    assert final["download_url"] is not None


@pytest.mark.asyncio
async def test_user_isolation(client: AsyncClient, db: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    user_a = await _create_user(db, f"isolation_a_{suffix}@test.com", "User A")
    user_b = await _create_user(db, f"isolation_b_{suffix}@test.com", "User B")

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "format": "csv",
    }, headers=_auth_header(user_a.id))
    assert res.status_code == 202
    report_id = res.json()["id"]

    res_b = await client.get(f"/api/v1/reports/{report_id}", headers=_auth_header(user_b.id))
    assert res_b.status_code == 404

    res_b_dl = await client.get(f"/api/v1/reports/{report_id}/download", headers=_auth_header(user_b.id))
    assert res_b_dl.status_code == 404


@pytest.mark.asyncio
async def test_list_ordering(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"ordering_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    ids = []
    for rtype in ["monthly_summary", "category_breakdown", "transactions"]:
        res = await client.post("/api/v1/reports", json={
            "type": rtype,
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "format": "csv",
        }, headers=headers)
        assert res.status_code == 202
        ids.append(res.json()["id"])

    await _run_worker_until_done(db)

    res = await client.get("/api/v1/reports", headers=headers)
    assert res.status_code == 200
    listed = res.json()
    assert len(listed) >= 3
    # newest first
    listed_ids = [r["id"] for r in listed]
    assert listed_ids[0] == ids[-1]
    assert listed_ids[-1] == ids[0]


@pytest.mark.asyncio
async def test_invalid_date_range(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"bad_date_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2026-03-01",
        "to_date": "2026-01-01",
        "format": "csv",
    }, headers=headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_date_range_too_long(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"long_range_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    res = await client.post("/api/v1/reports", json={
        "type": "monthly_summary",
        "from_date": "2024-01-01",
        "to_date": "2026-12-31",
        "format": "csv",
    }, headers=headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_all_report_types(client: AsyncClient, db: AsyncSession):
    user = await _create_user(db, f"all_types_{uuid.uuid4().hex[:8]}@test.com")
    headers = _auth_header(user.id)

    report_ids = []
    for rtype in ["monthly_summary", "category_breakdown", "budget_vs_actual", "transactions"]:
        for fmt in ["csv", "pdf"]:
            res = await client.post("/api/v1/reports", json={
                "type": rtype,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "format": fmt,
            }, headers=headers)
            assert res.status_code == 202, f"Failed for {rtype}/{fmt}: {res.text}"
            report_ids.append(res.json()["id"])

    await _run_worker_until_done(db)

    for report_id in report_ids:
        get_res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "succeeded"
