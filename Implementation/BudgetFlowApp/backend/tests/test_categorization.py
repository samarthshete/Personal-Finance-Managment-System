"""UC04 Categorize Expenses: categories CRUD, rule engine, manual override, user isolation."""

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

GOOD_CSV = "posted_date,amount,description,merchant\n2025-04-01,-12.50,Starbucks coffee,Starbucks\n2025-04-02,-55.00,Amazon purchase,Amazon\n"


async def _signup_and_login(client: AsyncClient, email: str) -> dict:
    await client.post("/api/v1/auth/signup", json={"email": email, "name": "T", "password": "SecurePass123!"})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": "SecurePass123!"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_account(client: AsyncClient, headers: dict) -> str:
    resp = await client.post(API_ACCOUNTS, json={"type": "bank", "name": "Checking"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def _csv_file(content: str, filename: str = "import.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


async def _import_transactions(
    db_session: AsyncSession, client: AsyncClient, headers: dict, acct_id: str, csv: str = GOOD_CSV,
):
    resp = await client.post(
        API_IMPORT,
        data={"account_id": acct_id},
        files=_csv_file(csv),
        headers=headers,
    )
    assert resp.status_code == 202
    await run_worker_until_done(db_session)
    return resp.json()


async def _get_transaction_ids(client: AsyncClient, headers: dict, acct_id: str):
    resp = await client.get(API_TRANSACTIONS, params={"account_id": acct_id}, headers=headers)
    assert resp.status_code == 200
    return [t["id"] for t in resp.json()]


# ---- Tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_category_and_list_includes_user_category(async_client: AsyncClient):
    email = f"cat_list_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.post(
        API_CATEGORIES,
        json={"name": "Groceries", "type": "expense", "rules": [{"pattern": "grocery", "match": "contains", "priority": 50}]},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Groceries"
    assert body["type"] == "expense"
    assert len(body["rules"]) == 1
    assert body["user_id"] is not None

    list_resp = await async_client.get(API_CATEGORIES, headers=headers)
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()]
    assert "Groceries" in names


@pytest.mark.asyncio
async def test_rule_categorization_sets_category_and_confidence(async_client: AsyncClient, db_session: AsyncSession):
    email = f"cat_rule_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    await async_client.post(
        API_CATEGORIES,
        json={"name": "Coffee", "type": "expense", "rules": [{"pattern": "starbucks", "match": "contains", "priority": 10}]},
        headers=headers,
    )

    await _import_transactions(db_session, async_client, headers, acct_id)
    tx_ids = await _get_transaction_ids(async_client, headers, acct_id)
    assert len(tx_ids) >= 1

    resp = await async_client.post(f"{API_TRANSACTIONS}/{tx_ids[0]}/categorize", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    if "starbucks" in body["description"].lower():
        assert body["category_id"] is not None
        assert body["categorization_source"] == "rule"
        assert float(body["category_confidence"]) == 1.0
        assert body["needs_manual"] is False
    else:
        resp2 = await async_client.post(f"{API_TRANSACTIONS}/{tx_ids[1]}/categorize", headers=headers)
        body2 = resp2.json()
        assert body2["category_id"] is not None
        assert body2["categorization_source"] == "rule"
        assert float(body2["category_confidence"]) == 1.0
        assert body2["needs_manual"] is False


@pytest.mark.asyncio
async def test_no_rule_sets_needs_manual_true(async_client: AsyncClient, db_session: AsyncSession):
    email = f"cat_noru_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    no_match_csv = "posted_date,amount,description\n2025-05-01,-20.00,Unique item nobody matches\n"
    await _import_transactions(db_session, async_client, headers, acct_id, no_match_csv)
    tx_ids = await _get_transaction_ids(async_client, headers, acct_id)
    assert len(tx_ids) == 1

    resp = await async_client.post(f"{API_TRANSACTIONS}/{tx_ids[0]}/categorize", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_id"] is None
    assert body["needs_manual"] is True
    assert body["categorization_source"] is None
    assert body["category_confidence"] is None


@pytest.mark.asyncio
async def test_manual_override_sets_source_manual_and_needs_manual_false(async_client: AsyncClient, db_session: AsyncSession):
    email = f"cat_man_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)
    acct_id = await _create_account(async_client, headers)

    cat_resp = await async_client.post(
        API_CATEGORIES, json={"name": "Misc", "type": "expense"}, headers=headers,
    )
    assert cat_resp.status_code == 201
    cat_id = cat_resp.json()["id"]

    csv = "posted_date,amount,description\n2025-06-01,-10.00,Random purchase\n"
    await _import_transactions(db_session, async_client, headers, acct_id, csv)
    tx_ids = await _get_transaction_ids(async_client, headers, acct_id)
    assert len(tx_ids) == 1

    resp = await async_client.post(
        f"{API_TRANSACTIONS}/{tx_ids[0]}/categorize",
        json={"category_id": cat_id},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_id"] == cat_id
    assert body["categorization_source"] == "manual"
    assert float(body["category_confidence"]) == 1.0
    assert body["needs_manual"] is False


@pytest.mark.asyncio
async def test_user_isolation_cannot_patch_other_users_category(async_client: AsyncClient):
    email_a = f"cat_iso_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"cat_iso_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a)
    headers_b = await _signup_and_login(async_client, email_b)

    resp = await async_client.post(
        API_CATEGORIES, json={"name": "Private Cat", "type": "expense"}, headers=headers_a,
    )
    assert resp.status_code == 201
    cat_id = resp.json()["id"]

    patch_resp = await async_client.patch(
        f"{API_CATEGORIES}/{cat_id}",
        json={"name": "Hacked"},
        headers=headers_b,
    )
    assert patch_resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_regex_rule_returns_400(async_client: AsyncClient):
    email = f"cat_badre_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.post(
        API_CATEGORIES,
        json={
            "name": "Bad Regex Cat",
            "type": "expense",
            "rules": [{"pattern": "[invalid(", "match": "regex", "priority": 10}],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_REGEX"


@pytest.mark.asyncio
async def test_patch_with_invalid_regex_returns_400(async_client: AsyncClient):
    email = f"cat_patchre_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    create_resp = await async_client.post(
        API_CATEGORIES, json={"name": "Valid First", "type": "expense"}, headers=headers,
    )
    assert create_resp.status_code == 201
    cat_id = create_resp.json()["id"]

    patch_resp = await async_client.patch(
        f"{API_CATEGORIES}/{cat_id}",
        json={"rules": [{"pattern": "(unclosed", "match": "regex"}]},
        headers=headers,
    )
    assert patch_resp.status_code == 400
    assert patch_resp.json()["detail"]["code"] == "INVALID_REGEX"


@pytest.mark.asyncio
async def test_user_isolation_cannot_categorize_other_users_transaction(async_client: AsyncClient, db_session: AsyncSession):
    email_a = f"cat_txiso_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"cat_txiso_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a)
    headers_b = await _signup_and_login(async_client, email_b)

    acct_a = await _create_account(async_client, headers_a)
    csv = "posted_date,amount,description\n2025-07-01,-5.00,Private tx\n"
    await _import_transactions(db_session, async_client, headers_a, acct_a, csv)
    tx_ids = await _get_transaction_ids(async_client, headers_a, acct_a)
    assert len(tx_ids) == 1

    resp = await async_client.post(
        f"{API_TRANSACTIONS}/{tx_ids[0]}/categorize",
        headers=headers_b,
    )
    assert resp.status_code == 404
