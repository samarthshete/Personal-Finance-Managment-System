"""Profile API: GET /me, PATCH /me — user isolation and field validation."""
import uuid

import pytest
from httpx import AsyncClient

API_AUTH = "/api/v1/auth"
API_ME = "/api/v1/me"


async def _signup_and_login(
    client: AsyncClient,
    email: str,
    name: str = "Test User",
) -> dict:
    await client.post(
        f"{API_AUTH}/signup",
        json={"email": email, "name": name, "password": "SecurePass123!"},
    )
    resp = await client.post(
        f"{API_AUTH}/login",
        data={"username": email, "password": "SecurePass123!"},
    )
    assert resp.status_code == 200, f"login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_me_returns_current_user(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email, "Alex Test")

    resp = await async_client.get(API_ME, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == email
    assert body["name"] == "Alex Test"
    assert body["preferred_currency"] == "USD"
    assert body["monthly_income_goal"] is None
    assert body["display_title"] is None
    assert "id" in body


@pytest.mark.asyncio
async def test_get_me_requires_auth(async_client: AsyncClient):
    resp = await async_client.get(API_ME)
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_me_updates_profile_fields(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.patch(
        API_ME,
        json={
            "name": "Updated Name",
            "preferred_currency": "EUR",
            "monthly_income_goal": 5000.0,
            "display_title": "Senior Engineer",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Updated Name"
    assert body["preferred_currency"] == "EUR"
    assert float(body["monthly_income_goal"]) == 5000.0
    assert body["display_title"] == "Senior Engineer"

    # Verify persistence: fetch again from DB
    resp2 = await async_client.get(API_ME, headers=headers)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["name"] == "Updated Name"
    assert body2["preferred_currency"] == "EUR"
    assert float(body2["monthly_income_goal"]) == 5000.0
    assert body2["display_title"] == "Senior Engineer"


@pytest.mark.asyncio
async def test_patch_me_partial_update_only_changes_sent_fields(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email, "Original Name")

    # First set display_title
    await async_client.patch(
        API_ME, json={"display_title": "CTO"}, headers=headers,
    )

    # Update only name — display_title must remain
    resp = await async_client.patch(
        API_ME, json={"name": "New Name"}, headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["display_title"] == "CTO"


@pytest.mark.asyncio
async def test_patch_me_rejects_negative_monthly_income_goal(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.patch(
        API_ME, json={"monthly_income_goal": -500}, headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_me_rejects_blank_name(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.patch(
        API_ME, json={"name": "   "}, headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_me_rejects_invalid_currency_length(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    # Too short (2 chars)
    r1 = await async_client.patch(
        API_ME, json={"preferred_currency": "US"}, headers=headers,
    )
    assert r1.status_code == 422

    # Too long (11 chars)
    r2 = await async_client.patch(
        API_ME, json={"preferred_currency": "TOOLONGCURR"}, headers=headers,
    )
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_patch_me_accepts_zero_monthly_income_goal(async_client: AsyncClient):
    email = f"prof_{uuid.uuid4().hex[:8]}@test.com"
    headers = await _signup_and_login(async_client, email)

    resp = await async_client.patch(
        API_ME, json={"monthly_income_goal": 0}, headers=headers,
    )
    assert resp.status_code == 200
    assert float(resp.json()["monthly_income_goal"]) == 0.0


# ---------------------------------------------------------------------------
# User isolation: each token sees only its own profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_is_token_scoped_to_current_user(async_client: AsyncClient):
    email_a = f"prof_a_{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"prof_b_{uuid.uuid4().hex[:8]}@test.com"
    headers_a = await _signup_and_login(async_client, email_a, "User A")
    headers_b = await _signup_and_login(async_client, email_b, "User B")

    # Set distinct profile for User A
    await async_client.patch(
        API_ME, json={"display_title": "A's exclusive title"}, headers=headers_a,
    )

    # User B must see their own profile, not A's
    resp_b = await async_client.get(API_ME, headers=headers_b)
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["email"] == email_b
    assert body_b["name"] == "User B"
    assert body_b["display_title"] is None

    # User A must still see their own data
    resp_a = await async_client.get(API_ME, headers=headers_a)
    assert resp_a.json()["display_title"] == "A's exclusive title"
    assert resp_a.json()["email"] == email_a
