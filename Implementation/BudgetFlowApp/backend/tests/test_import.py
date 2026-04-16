"""UC03 Import Transactions: CSV import, dedup, user isolation. Async via UC09 worker."""

import uuid
import io

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import run_worker_until_done

API_ACCOUNTS = "/api/v1/accounts"
API_IMPORT = "/api/v1/transactions/import"
API_SESSIONS = "/api/v1/transactions/import/sessions"

GOOD_CSV = "posted_date,amount,description,merchant\n2025-01-15,-42.50,Coffee Shop,Starbucks\n2025-01-16,1500.00,Salary deposit,\n"


async def _signup_and_login(client: AsyncClient, email: str):
    await client.post("/api/v1/auth/signup", json={"email": email, "name": "T", "password": "SecurePass123!"})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": "SecurePass123!"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_account(client: AsyncClient, headers: dict) -> str:
    resp = await client.post(API_ACCOUNTS, json={"type": "bank", "name": "Checking"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def _csv_file(content: str, filename: str = "import.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


@pytest.mark.asyncio
async def test_csv_import_happy_path(async_client: AsyncClient, db_session: AsyncSession):
    email = f"imp_ok_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    resp = await async_client.post(
        API_IMPORT,
        data={"account_id": acct_id},
        files=_csv_file(GOOD_CSV),
        headers=headers,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"] is not None
    assert body["import_session_id"] is not None

    await run_worker_until_done(db_session)

    sess_resp = await async_client.get(f"{API_SESSIONS}/{body['import_session_id']}", headers=headers)
    assert sess_resp.status_code == 200
    session = sess_resp.json()
    assert session["status"] == "completed"
    assert session["total_rows"] == 2
    assert session["imported_count"] == 2
    assert session["duplicate_count"] == 0
    assert session["failed_count"] == 0

    list_resp = await async_client.get(API_SESSIONS, headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


@pytest.mark.asyncio
async def test_csv_import_rejects_missing_columns(async_client: AsyncClient):
    email = f"imp_bad_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    bad_csv = "date,amt\n2025-01-01,100\n"
    resp = await async_client.post(
        API_IMPORT,
        data={"account_id": acct_id},
        files=_csv_file(bad_csv),
        headers=headers,
    )
    assert resp.status_code == 422
    assert "Missing required columns" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_csv_import_user_isolation(async_client: AsyncClient):
    email_a = f"imp_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"imp_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a)
    headers_b = await _signup_and_login(async_client, email_b)
    acct_a = await _create_account(async_client, headers_a)

    resp = await async_client.post(
        API_IMPORT,
        data={"account_id": acct_a},
        files=_csv_file(GOOD_CSV),
        headers=headers_b,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csv_import_duplicate_detection(async_client: AsyncClient, db_session: AsyncSession):
    email = f"imp_dup_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    one_row = "posted_date,amount,description\n2025-03-01,99.99,Duplicate me\n"

    r1 = await async_client.post(
        API_IMPORT, data={"account_id": acct_id}, files=_csv_file(one_row, "a.csv"), headers=headers,
    )
    assert r1.status_code == 202
    await run_worker_until_done(db_session)
    sess1 = await async_client.get(f"{API_SESSIONS}/{r1.json()['import_session_id']}", headers=headers)
    assert sess1.json()["imported_count"] == 1

    r2 = await async_client.post(
        API_IMPORT, data={"account_id": acct_id}, files=_csv_file(one_row, "b.csv"), headers=headers,
    )
    assert r2.status_code == 202
    await run_worker_until_done(db_session)
    sess2 = await async_client.get(f"{API_SESSIONS}/{r2.json()['import_session_id']}", headers=headers)
    body = sess2.json()
    assert body["imported_count"] == 0
    assert body["duplicate_count"] == 1


@pytest.mark.asyncio
async def test_list_sessions_user_isolated(async_client: AsyncClient, db_session: AsyncSession):
    email_a = f"ses_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"ses_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a)
    headers_b = await _signup_and_login(async_client, email_b)
    acct_a = await _create_account(async_client, headers_a)

    await async_client.post(
        API_IMPORT, data={"account_id": acct_a}, files=_csv_file(GOOD_CSV), headers=headers_a,
    )
    await run_worker_until_done(db_session)

    resp_a = await async_client.get(API_SESSIONS, headers=headers_a)
    assert resp_a.status_code == 200
    assert len(resp_a.json()) == 1

    resp_b = await async_client.get(API_SESSIONS, headers=headers_b)
    assert resp_b.status_code == 200
    assert len(resp_b.json()) == 0
