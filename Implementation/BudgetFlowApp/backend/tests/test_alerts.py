"""UC06 Budget Alerts: lazy generation, dedup, user isolation, mark read."""

import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import run_worker_until_done

API_ACCOUNTS = "/api/v1/accounts"
API_CATEGORIES = "/api/v1/categories"
API_TRANSACTIONS = "/api/v1/transactions"
API_IMPORT = "/api/v1/transactions/import"
API_BUDGETS = "/api/v1/budgets"
API_ALERTS = "/api/v1/alerts"


async def _signup_and_login(client: AsyncClient, email: str) -> dict:
    await client.post("/api/v1/auth/signup", json={"email": email, "name": "T", "password": "SecurePass123!"})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": "SecurePass123!"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_account(client: AsyncClient, headers: dict) -> str:
    resp = await client.post(API_ACCOUNTS, json={"type": "bank", "name": "Checking"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_category(client: AsyncClient, headers: dict, name: str = "Food") -> str:
    resp = await client.post(API_CATEGORIES, json={"name": name, "type": "expense"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def _csv_file(content: str, filename: str = "import.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


async def _import_and_categorize(
    db_session: AsyncSession,
    client: AsyncClient, headers: dict, acct_id: str, cat_id: str,
    csv_content: str,
):
    """Import transactions via CSV, then manually assign them to the category."""
    resp = await client.post(
        API_IMPORT,
        data={"account_id": acct_id},
        files=_csv_file(csv_content),
        headers=headers,
    )
    assert resp.status_code == 202
    await run_worker_until_done(db_session)

    tx_resp = await client.get(API_TRANSACTIONS, params={"account_id": acct_id}, headers=headers)
    assert tx_resp.status_code == 200
    for tx in tx_resp.json():
        await client.post(
            f"{API_TRANSACTIONS}/{tx['id']}/categorize",
            json={"category_id": cat_id},
            headers=headers,
        )


# ---- Tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_generated_when_threshold_crossed(async_client: AsyncClient, db_session: AsyncSession):
    email = f"alrt_gen_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)
    cat_id = await _create_category(async_client, headers)

    csv = "posted_date,amount,description\n2026-03-10,-90.00,Lunch expense\n"
    await _import_and_categorize(db_session, async_client, headers, acct_id, cat_id, csv)

    budget_resp = await async_client.post(API_BUDGETS, json={
        "name": "March",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "thresholds": [0.8, 0.9, 1.0],
        "items": [{"category_id": cat_id, "limit_amount": 100}],
    }, headers=headers)
    assert budget_resp.status_code == 201

    alerts_resp = await async_client.get(API_ALERTS, headers=headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) >= 2
    thresholds_hit = {float(a["threshold_percent"]) for a in alerts}
    assert 0.8 in thresholds_hit
    assert 0.9 in thresholds_hit


@pytest.mark.asyncio
async def test_no_duplicate_alerts_for_same_threshold(async_client: AsyncClient, db_session: AsyncSession):
    email = f"alrt_dup_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)
    cat_id = await _create_category(async_client, headers)

    csv = "posted_date,amount,description\n2026-04-05,-120.00,Big expense\n"
    await _import_and_categorize(db_session, async_client, headers, acct_id, cat_id, csv)

    await async_client.post(API_BUDGETS, json={
        "name": "April",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "thresholds": [0.8, 1.0],
        "items": [{"category_id": cat_id, "limit_amount": 100}],
    }, headers=headers)

    resp1 = await async_client.get(API_ALERTS, headers=headers)
    count1 = len(resp1.json())

    resp2 = await async_client.get(API_ALERTS, headers=headers)
    count2 = len(resp2.json())
    assert count1 == count2


@pytest.mark.asyncio
async def test_alerts_user_isolated(async_client: AsyncClient, db_session: AsyncSession):
    email_a = f"alrt_iso_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"alrt_iso_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a)
    headers_b = await _signup_and_login(async_client, email_b)
    acct_a = await _create_account(async_client, headers_a)
    cat_a = await _create_category(async_client, headers_a)

    csv = "posted_date,amount,description\n2026-05-10,-200.00,Expensive\n"
    await _import_and_categorize(db_session, async_client, headers_a, acct_a, cat_a, csv)

    await async_client.post(API_BUDGETS, json={
        "name": "May",
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "thresholds": [0.8, 1.0],
        "items": [{"category_id": cat_a, "limit_amount": 100}],
    }, headers=headers_a)

    alerts_a = await async_client.get(API_ALERTS, headers=headers_a)
    assert len(alerts_a.json()) >= 1

    alerts_b = await async_client.get(API_ALERTS, headers=headers_b)
    assert len(alerts_b.json()) == 0


@pytest.mark.asyncio
async def test_mark_alert_read(async_client: AsyncClient, db_session: AsyncSession):
    email = f"alrt_read_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)
    cat_id = await _create_category(async_client, headers)

    csv = "posted_date,amount,description\n2026-06-15,-150.00,Over budget\n"
    await _import_and_categorize(db_session, async_client, headers, acct_id, cat_id, csv)

    await async_client.post(API_BUDGETS, json={
        "name": "June",
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
        "thresholds": [1.0],
        "items": [{"category_id": cat_id, "limit_amount": 100}],
    }, headers=headers)

    alerts_resp = await async_client.get(API_ALERTS, headers=headers)
    alerts = alerts_resp.json()
    assert len(alerts) >= 1
    alert_id = alerts[0]["id"]
    assert alerts[0]["is_read"] is False

    mark_resp = await async_client.patch(f"{API_ALERTS}/{alert_id}/read", headers=headers)
    assert mark_resp.status_code == 200
    assert mark_resp.json()["is_read"] is True

    unread = await async_client.get(API_ALERTS, params={"is_read": False}, headers=headers)
    read_ids = {a["id"] for a in unread.json()}
    assert alert_id not in read_ids


@pytest.mark.asyncio
async def test_no_alert_if_under_threshold(async_client: AsyncClient, db_session: AsyncSession):
    email = f"alrt_none_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)
    cat_id = await _create_category(async_client, headers)

    csv = "posted_date,amount,description\n2026-07-10,-5.00,Small purchase\n"
    await _import_and_categorize(db_session, async_client, headers, acct_id, cat_id, csv)

    await async_client.post(API_BUDGETS, json={
        "name": "July",
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "thresholds": [0.8, 0.9, 1.0],
        "items": [{"category_id": cat_id, "limit_amount": 1000}],
    }, headers=headers)

    alerts_resp = await async_client.get(API_ALERTS, headers=headers)
    assert alerts_resp.status_code == 200
    assert len(alerts_resp.json()) == 0
