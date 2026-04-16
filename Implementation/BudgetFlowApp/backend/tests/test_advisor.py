"""Advisor chat: sessions, messages, tool calling, isolation."""
import uuid
from datetime import timedelta
from decimal import Decimal

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
from app.models.account import FinancialAccount
from app.models.transaction import Transaction
from app.api.v1.advisor import get_llm
from app.services.advisor.llm_provider import FakeLLM


@pytest_asyncio.fixture(scope="function")
async def db():
    engine = create_async_engine(settings.effective_database_url, echo=False, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, name="Test", hashed_password=security.get_password_hash("Pass1234!"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _headers(user_id) -> dict:
    return {"Authorization": f"Bearer {security.create_access_token(user_id)}"}


@pytest_asyncio.fixture(scope="function")
async def client_and_llm(db):
    fake = FakeLLM()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm] = lambda: fake

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, fake

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_session_and_user_isolation(client_and_llm, db):
    client, _ = client_and_llm
    user_a = await _make_user(db, f"adv_a_{uuid.uuid4().hex[:6]}@test.com")
    user_b = await _make_user(db, f"adv_b_{uuid.uuid4().hex[:6]}@test.com")

    res = await client.post("/api/v1/advisor/sessions", json={"title": "My chat"}, headers=_headers(user_a.id))
    assert res.status_code == 201
    sid = res.json()["id"]

    list_a = await client.get("/api/v1/advisor/sessions", headers=_headers(user_a.id))
    assert any(s["id"] == sid for s in list_a.json())

    list_b = await client.get("/api/v1/advisor/sessions", headers=_headers(user_b.id))
    assert not any(s["id"] == sid for s in list_b.json())

    get_b = await client.get(f"/api/v1/advisor/sessions/{sid}", headers=_headers(user_b.id))
    assert get_b.status_code == 404


@pytest.mark.asyncio
async def test_send_message_stores_in_db(client_and_llm, db):
    client, fake = client_and_llm
    fake.push(FakeLLM.make_text_response("Your total spending is $0.00 for the period."))

    user = await _make_user(db, f"adv_msg_{uuid.uuid4().hex[:6]}@test.com")
    headers = _headers(user.id)

    res = await client.post("/api/v1/advisor/message", json={
        "content": "How much did I spend?",
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["message"]["role"] == "assistant"
    assert "spending" in data["message"]["content"].lower()

    session_res = await client.get(f"/api/v1/advisor/sessions/{data['session_id']}", headers=headers)
    messages = session_res.json()["messages"]
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_tool_calling_path(client_and_llm, db):
    client, fake = client_and_llm

    fake.push(FakeLLM.make_tool_call_response("get_summary", {
        "date_from": "2026-01-01", "date_to": "2026-01-31",
    }))
    fake.push(FakeLLM.make_text_response(
        "Your total spending from Jan 1-31 was $0.00. No transactions found for this period."
    ))

    user = await _make_user(db, f"adv_tool_{uuid.uuid4().hex[:6]}@test.com")
    headers = _headers(user.id)

    res = await client.post("/api/v1/advisor/message", json={
        "content": "How much did I spend in January 2026?",
    }, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "$0.00" in data["message"]["content"]

    session_res = await client.get(f"/api/v1/advisor/sessions/{data['session_id']}", headers=headers)
    messages = session_res.json()["messages"]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert tool_msgs[0]["tool_name"] == "get_summary"


@pytest.mark.asyncio
async def test_tool_missing_args_clarification(client_and_llm, db):
    client, fake = client_and_llm

    fake.push(FakeLLM.make_text_response(
        "Could you specify a date range? For example, 'last month' or 'January 2026'."
    ))

    user = await _make_user(db, f"adv_clar_{uuid.uuid4().hex[:6]}@test.com")
    res = await client.post("/api/v1/advisor/message", json={
        "content": "Show me trends",
    }, headers=_headers(user.id))
    assert res.status_code == 200
    assert "date" in res.json()["message"]["content"].lower()


@pytest.mark.asyncio
async def test_session_isolation_messages(client_and_llm, db):
    client, fake = client_and_llm
    fake.push(FakeLLM.make_text_response("Answer for user A"))
    fake.push(FakeLLM.make_text_response("Answer for user B"))

    user_a = await _make_user(db, f"adv_isomsg_a_{uuid.uuid4().hex[:6]}@test.com")
    user_b = await _make_user(db, f"adv_isomsg_b_{uuid.uuid4().hex[:6]}@test.com")

    res_a = await client.post("/api/v1/advisor/message", json={
        "content": "Question from A",
    }, headers=_headers(user_a.id))
    assert res_a.status_code == 200
    sid_a = res_a.json()["session_id"]

    res_b = await client.post("/api/v1/advisor/message", json={
        "content": "Question from B",
    }, headers=_headers(user_b.id))
    assert res_b.status_code == 200

    get_b = await client.get(f"/api/v1/advisor/sessions/{sid_a}", headers=_headers(user_b.id))
    assert get_b.status_code == 404


# ---------------------------------------------------------------------------
# Recommendation tool integration tests
# ---------------------------------------------------------------------------

async def _seed_acct(db: AsyncSession, user_id: uuid.UUID, income=5000.0, spending=3000.0, balance=10000.0):
    import hashlib
    from datetime import date, timedelta

    acct = FinancialAccount(user_id=user_id, type="bank", name="Checking", currency="USD", balance=Decimal(str(balance)))
    db.add(acct)
    await db.flush()
    recent = date.today() - timedelta(days=15)
    fp1 = hashlib.sha256(f"inc-{user_id}-{uuid.uuid4()}".encode()).hexdigest()[:64]
    db.add(Transaction(account_id=acct.id, posted_date=recent, amount=Decimal(str(income)),
                       description="Salary", description_normalized="salary", fingerprint=fp1, currency="USD"))
    fp2 = hashlib.sha256(f"exp-{user_id}-{uuid.uuid4()}".encode()).hexdigest()[:64]
    db.add(Transaction(account_id=acct.id, posted_date=recent, amount=Decimal(str(-spending)),
                       description="Expenses", description_normalized="expenses", fingerprint=fp2, currency="USD"))
    await db.commit()


@pytest.mark.asyncio
async def test_advisor_run_recommendation_tool(client_and_llm, db):
    """Advisor calls run_recommendation tool and returns grounded answer."""
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_rec_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=8000, spending=3000, balance=20000)

    fake.push(FakeLLM.make_tool_call_response("run_recommendation", {"horizon_months": 60}))
    fake.push(FakeLLM.make_text_response(
        "Based on your data, all safety gates pass. Your risk bucket is balanced with $4,000/mo investable."
    ))

    res = await client.post("/api/v1/advisor/message", json={
        "content": "Should I start investing?",
    }, headers=_headers(user.id))
    assert res.status_code == 200

    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    messages = session_res.json()["messages"]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert tool_msgs[0]["tool_name"] == "run_recommendation"
    payload = tool_msgs[0]["tool_payload"]
    assert "gates" in payload
    assert "run_id" in payload


@pytest.mark.asyncio
async def test_advisor_get_latest_recommendation_tool(client_and_llm, db):
    """Advisor calls get_latest_recommendation after a run exists."""
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_latest_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=8000, spending=3000, balance=20000)

    # First generate a run via API
    from app.services import recommendation_service
    await recommendation_service.execute_run(db, user.id)

    fake.push(FakeLLM.make_tool_call_response("get_latest_recommendation", {}))
    fake.push(FakeLLM.make_text_response("Your latest recommendation shows a balanced portfolio."))

    res = await client.post("/api/v1/advisor/message", json={
        "content": "What was my last investment recommendation?",
    }, headers=_headers(user.id))
    assert res.status_code == 200

    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    messages = session_res.json()["messages"]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert tool_msgs[0]["tool_name"] == "get_latest_recommendation"
    payload = tool_msgs[0]["tool_payload"]
    assert "run_id" in payload
    assert "action_items" in payload


@pytest.mark.asyncio
async def test_advisor_blocked_user_gets_action_items(client_and_llm, db):
    """When safety gates fail, tool returns action_items not allocation."""
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_blk_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=5000, spending=3000, balance=500)

    fake.push(FakeLLM.make_tool_call_response("run_recommendation", {"horizon_months": 60}))
    fake.push(FakeLLM.make_text_response(
        "Your emergency fund is too low. Build 3 months of expenses before investing."
    ))

    res = await client.post("/api/v1/advisor/message", json={
        "content": "I want to start investing",
    }, headers=_headers(user.id))
    assert res.status_code == 200

    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    messages = session_res.json()["messages"]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    payload = tool_msgs[0]["tool_payload"]
    assert "allocation" not in payload
    assert "action_items" in payload
    assert len(payload["safety_warnings"]) > 0


# ---------------------------------------------------------------------------
# Date resolution and category aggregation tests (Bug fixes)
# ---------------------------------------------------------------------------

def test_system_prompt_resolves_last_month_correctly():
    """get_system_prompt must embed the correct date range for 'last month'."""
    import calendar
    from datetime import date as _date
    from app.services.advisor.prompt import get_system_prompt

    # Use a fixed reference date so the test is fully deterministic.
    ref = _date(2026, 3, 2)  # March 2, 2026 → last month = February 2026
    prompt = get_system_prompt(ref)

    # Today's date is present
    assert "2026-03-02" in prompt

    # Last month = 2026-02-01 → 2026-02-28
    assert "2026-02-01" in prompt
    assert "2026-02-28" in prompt

    # This month = 2026-03-01 → 2026-03-31
    assert "2026-03-01" in prompt
    assert "2026-03-31" in prompt

    # Last year = 2025-01-01 → 2025-12-31
    assert "2025-01-01" in prompt
    assert "2025-12-31" in prompt


def test_system_prompt_resolves_january_boundary():
    """When today is in January, last month must be December of the prior year."""
    from datetime import date as _date
    from app.services.advisor.prompt import get_system_prompt

    ref = _date(2026, 1, 15)  # Jan 15 → last month = December 2025
    prompt = get_system_prompt(ref)

    assert "2025-12-01" in prompt
    assert "2025-12-31" in prompt


def test_system_prompt_last_quarter_q1():
    """Q1 boundary: last quarter for a date in Q1 must be Q4 of previous year."""
    from datetime import date as _date
    from app.services.advisor.prompt import get_system_prompt

    ref = _date(2026, 2, 10)  # February = Q1 → last quarter = Q4 2025
    prompt = get_system_prompt(ref)

    assert "2025-10-01" in prompt
    assert "2025-12-31" in prompt


def test_build_context_system_message_contains_today():
    """_build_context must inject today's real server date into the system message."""
    from datetime import date as _date
    from app.services.advisor.advisor_service import _build_context

    today = _date.today().isoformat()
    context = _build_context([])

    assert context[0]["role"] == "system"
    assert today in context[0]["content"], (
        f"Expected today ({today}) in system prompt but got: {context[0]['content'][:200]}"
    )


@pytest.mark.asyncio
async def test_get_summary_expenses_only_excludes_income(db):
    """get_summary(expenses_only=True) must exclude income transactions."""
    import hashlib
    from datetime import date as _date, timedelta
    from app.services import analytics_service

    user = await _make_user(db, f"analytics_exp_{uuid.uuid4().hex[:6]}@test.com")

    acct = FinancialAccount(
        user_id=user.id, type="bank", name="Checking", currency="USD",
        balance=Decimal("5000"),
    )
    db.add(acct)
    await db.flush()

    today = _date.today()
    income_fp  = hashlib.sha256(f"income_{uuid.uuid4()}".encode()).hexdigest()[:64]
    expense_fp = hashlib.sha256(f"expense_{uuid.uuid4()}".encode()).hexdigest()[:64]

    db.add(Transaction(
        account_id=acct.id, posted_date=today - timedelta(days=5),
        amount=Decimal("5000.00"), description="Salary Deposit",
        description_normalized="salary deposit", fingerprint=income_fp, currency="USD",
    ))
    db.add(Transaction(
        account_id=acct.id, posted_date=today - timedelta(days=3),
        amount=Decimal("-200.00"), description="Grocery Store",
        description_normalized="grocery store", fingerprint=expense_fp, currency="USD",
    ))
    await db.commit()

    # expenses_only=True → only the $200 debit
    result = await analytics_service.get_summary(db, user.id, expenses_only=True)
    assert float(result["total_spending"]) == pytest.approx(200.0)

    # No income category in by_category when expenses_only
    for cat in result["by_category"]:
        assert cat["category_type"] != "income", (
            f"Income category {cat['category_name']} must not appear with expenses_only=True"
        )

    # expenses_only=False → includes both; total = 5200
    result_all = await analytics_service.get_summary(db, user.id, expenses_only=False)
    assert float(result_all["total_spending"]) == pytest.approx(5200.0)


@pytest.mark.asyncio
async def test_get_summary_category_names_present(db):
    """by_category entries must carry category_name, not raw UUIDs only."""
    import hashlib
    from datetime import date as _date, timedelta
    from app.services import analytics_service
    from app.models.category import Category as CategoryModel

    user = await _make_user(db, f"analytics_cat_{uuid.uuid4().hex[:6]}@test.com")

    acct = FinancialAccount(
        user_id=user.id, type="bank", name="Checking", currency="USD",
        balance=Decimal("2000"),
    )
    db.add(acct)
    cat = CategoryModel(user_id=user.id, name="Dining Out", type="expense", rules=[])
    db.add(cat)
    await db.flush()

    fp = hashlib.sha256(f"dining_{uuid.uuid4()}".encode()).hexdigest()[:64]
    db.add(Transaction(
        account_id=acct.id, posted_date=_date.today(),
        amount=Decimal("-45.00"), description="Restaurant",
        description_normalized="restaurant", fingerprint=fp, currency="USD",
        category_id=cat.id,
    ))
    await db.commit()

    result = await analytics_service.get_summary(db, user.id, expenses_only=True)
    assert len(result["by_category"]) == 1
    assert result["by_category"][0]["category_name"] == "Dining Out"
    assert result["by_category"][0]["category_type"] == "expense"
    assert float(result["by_category"][0]["total"]) == pytest.approx(45.0)


@pytest.mark.asyncio
async def test_get_summary_user_isolation(db):
    """get_summary must never return another user's transactions."""
    import hashlib
    from datetime import date as _date, timedelta
    from app.services import analytics_service

    user_a = await _make_user(db, f"iso_a_{uuid.uuid4().hex[:6]}@test.com")
    user_b = await _make_user(db, f"iso_b_{uuid.uuid4().hex[:6]}@test.com")

    for user, amount in [(user_a, Decimal("-100.00")), (user_b, Decimal("-999.00"))]:
        acct = FinancialAccount(
            user_id=user.id, type="bank", name="Checking", currency="USD",
            balance=Decimal("1000"),
        )
        db.add(acct)
        await db.flush()
        fp = hashlib.sha256(f"iso_{user.id}_{uuid.uuid4()}".encode()).hexdigest()[:64]
        db.add(Transaction(
            account_id=acct.id, posted_date=_date.today(),
            amount=amount, description="Purchase",
            description_normalized="purchase", fingerprint=fp, currency="USD",
        ))
    await db.commit()

    result_a = await analytics_service.get_summary(db, user_a.id, expenses_only=True)
    assert float(result_a["total_spending"]) == pytest.approx(100.0), (
        "User A must not see User B's $999 transaction"
    )


@pytest.mark.asyncio
async def test_advisor_last_month_tool_call_uses_resolved_dates(client_and_llm, db):
    """When the user says 'last month', the system prompt provides exact dates.
    Verify the prompt contains the correct resolved range and that a tool call
    with those dates flows through the pipeline without error.
    """
    import calendar
    from datetime import date as _date
    from app.services.advisor.prompt import get_system_prompt

    client, fake = client_and_llm
    user = await _make_user(db, f"adv_lm_{uuid.uuid4().hex[:6]}@test.com")

    today = _date.today()
    if today.month == 1:
        lm_year, lm_month = today.year - 1, 12
    else:
        lm_year, lm_month = today.year, today.month - 1
    lm_start = _date(lm_year, lm_month, 1).isoformat()
    lm_end   = _date(lm_year, lm_month, calendar.monthrange(lm_year, lm_month)[1]).isoformat()

    # System prompt must contain these resolved dates
    prompt = get_system_prompt(today)
    assert lm_start in prompt, f"Expected {lm_start} in system prompt"
    assert lm_end   in prompt, f"Expected {lm_end} in system prompt"

    # Simulate: LLM resolves "last month" and calls get_summary correctly
    fake.push(FakeLLM.make_tool_call_response("get_summary", {
        "date_from": lm_start,
        "date_to":   lm_end,
        "expenses_only": True,
    }))
    fake.push(FakeLLM.make_text_response(
        f"Your total spending last month ({lm_start} to {lm_end}) was $0.00 — "
        "no transactions found for that period."
    ))

    res = await client.post(
        "/api/v1/advisor/message",
        json={"content": "How much did I spend last month?"},
        headers=_headers(user.id),
    )
    assert res.status_code == 200
    data = res.json()
    assert "last month" in data["message"]["content"].lower()

    session_res = await client.get(
        f"/api/v1/advisor/sessions/{data['session_id']}",
        headers=_headers(user.id),
    )
    tool_msgs = [m for m in session_res.json()["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_name"] == "get_summary"


@pytest.mark.asyncio
async def test_advisor_top_category_uses_expenses_only(client_and_llm, db):
    """Category-spend question must call get_summary with expenses_only=true and
    only return user-scoped expense categories — never income like Salary.
    """
    import hashlib
    from datetime import date as _date, timedelta
    from app.models.category import Category as CategoryModel

    client, fake = client_and_llm
    user = await _make_user(db, f"adv_topcat_{uuid.uuid4().hex[:6]}@test.com")

    acct = FinancialAccount(
        user_id=user.id, type="bank", name="Checking",
        currency="USD", balance=Decimal("10000"),
    )
    db.add(acct)
    salary_cat  = CategoryModel(user_id=user.id, name="Salary",    type="income",  rules=[])
    grocery_cat = CategoryModel(user_id=user.id, name="Groceries", type="expense", rules=[])
    db.add_all([salary_cat, grocery_cat])
    await db.flush()

    today = _date.today()
    txns = [
        Transaction(
            account_id=acct.id, posted_date=today - timedelta(days=10),
            amount=Decimal("8000.00"), description="Salary Deposit",
            description_normalized="salary deposit", currency="USD",
            category_id=salary_cat.id,
            fingerprint=hashlib.sha256(f"sal_{uuid.uuid4()}".encode()).hexdigest()[:64],
        ),
        Transaction(
            account_id=acct.id, posted_date=today - timedelta(days=5),
            amount=Decimal("-320.00"), description="Whole Foods",
            description_normalized="whole foods", currency="USD",
            category_id=grocery_cat.id,
            fingerprint=hashlib.sha256(f"groc_{uuid.uuid4()}".encode()).hexdigest()[:64],
        ),
    ]
    db.add_all(txns)
    await db.commit()

    # Simulate: LLM correctly calls get_summary with expenses_only=true
    last90 = (today - timedelta(days=89)).isoformat()
    fake.push(FakeLLM.make_tool_call_response("get_summary", {
        "date_from":     last90,
        "date_to":       today.isoformat(),
        "expenses_only": True,
    }))
    fake.push(FakeLLM.make_text_response(
        "Your top spending category is Groceries at $320.00 over the last 90 days."
    ))

    res = await client.post(
        "/api/v1/advisor/message",
        json={"content": "Which category am I spending the most on?"},
        headers=_headers(user.id),
    )
    assert res.status_code == 200

    session_res = await client.get(
        f"/api/v1/advisor/sessions/{res.json()['session_id']}",
        headers=_headers(user.id),
    )
    tool_msgs = [m for m in session_res.json()["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    tool_result = tool_msgs[0]["tool_payload"]

    # The tool executed against real DB with expenses_only=True
    # so Salary (income) must NOT appear in by_category
    by_cat = tool_result.get("by_category", [])
    category_names = [c["category_name"] for c in by_cat]
    assert "Salary" not in category_names, (
        "Salary (income) must be excluded when expenses_only=True"
    )
    assert "Groceries" in category_names
    # Top category by total
    assert by_cat[0]["category_name"] == "Groceries"
    assert float(by_cat[0]["total"]) == pytest.approx(320.0)


@pytest.mark.asyncio
async def test_advisor_compare_this_month_vs_last_month(client_and_llm, db):
    import hashlib
    from datetime import date as _date
    from app.models.category import Category as CategoryModel

    client, fake = client_and_llm
    user = await _make_user(db, f"adv_cmp_{uuid.uuid4().hex[:6]}@test.com")
    acct = FinancialAccount(user_id=user.id, type="bank", name="Checking", currency="USD", balance=Decimal("5000"))
    db.add(acct)
    cat = CategoryModel(user_id=user.id, name="Groceries", type="expense", rules=[])
    db.add(cat)
    await db.flush()

    today = _date.today()
    this_start = today.replace(day=1)
    if this_start.month == 1:
        last_start = _date(this_start.year - 1, 12, 1)
    else:
        last_start = _date(this_start.year, this_start.month - 1, 1)
    last_end = this_start - timedelta(days=1)

    db.add(Transaction(
        account_id=acct.id, posted_date=this_start + timedelta(days=2),
        amount=Decimal("-300.00"), description="Groceries", description_normalized="groceries",
        fingerprint=hashlib.sha256(f"cmp1_{uuid.uuid4()}".encode()).hexdigest()[:64], currency="USD", category_id=cat.id,
    ))
    db.add(Transaction(
        account_id=acct.id, posted_date=last_start + timedelta(days=2),
        amount=Decimal("-500.00"), description="Groceries", description_normalized="groceries",
        fingerprint=hashlib.sha256(f"cmp2_{uuid.uuid4()}".encode()).hexdigest()[:64], currency="USD", category_id=cat.id,
    ))
    await db.commit()

    fake.push(FakeLLM.make_tool_call_response("compare_spending", {
        "period_a": {"date_from": last_start.isoformat(), "date_to": last_end.isoformat()},
        "period_b": {"date_from": this_start.isoformat(), "date_to": today.isoformat()},
        "expenses_only": True,
    }))
    fake.push(FakeLLM.make_text_response("Direct answer: This month is better.\nInsights:\n- Spending is down.\nNext actions:\n- Keep grocery plan."))

    res = await client.post("/api/v1/advisor/message", json={"content": "How does this month compare to last month?"}, headers=_headers(user.id))
    assert res.status_code == 200

    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    tool_payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    assert tool_payload["better_or_worse"] == "better"
    assert tool_payload["delta_amount"] < 0


@pytest.mark.asyncio
async def test_advisor_top_category_increase_tool(client_and_llm, db):
    import hashlib
    from datetime import date as _date
    from app.models.category import Category as CategoryModel

    client, fake = client_and_llm
    user = await _make_user(db, f"adv_topinc_{uuid.uuid4().hex[:6]}@test.com")
    acct = FinancialAccount(user_id=user.id, type="bank", name="Checking", currency="USD", balance=Decimal("5000"))
    db.add(acct)
    groceries = CategoryModel(user_id=user.id, name="Groceries", type="expense", rules=[])
    dining = CategoryModel(user_id=user.id, name="Dining", type="expense", rules=[])
    db.add_all([groceries, dining])
    await db.flush()

    end = _date.today()
    start = end - timedelta(days=29)
    prev_start = start - timedelta(days=30)

    txns = [
        (start + timedelta(days=2), "-420.00", groceries.id, "groc_cur"),
        (start + timedelta(days=5), "-120.00", dining.id, "din_cur"),
        (prev_start + timedelta(days=2), "-140.00", groceries.id, "groc_prev"),
        (prev_start + timedelta(days=5), "-110.00", dining.id, "din_prev"),
    ]
    for d, amt, cat_id, seed in txns:
        db.add(Transaction(
            account_id=acct.id, posted_date=d, amount=Decimal(amt), description="Spend",
            description_normalized="spend", currency="USD", category_id=cat_id,
            fingerprint=hashlib.sha256(f"{seed}_{uuid.uuid4()}".encode()).hexdigest()[:64],
        ))
    await db.commit()

    fake.push(FakeLLM.make_tool_call_response("top_category_changes", {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "compare_to_previous": True,
    }))
    fake.push(FakeLLM.make_text_response("Direct answer: Groceries increased the most.\nInsights:\n- Groceries rose sharply.\nNext actions:\n- Cap grocery trips."))

    res = await client.post("/api/v1/advisor/message", json={"content": "Which category increased the most?"}, headers=_headers(user.id))
    assert res.status_code == 200
    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    assert payload["top_increases"][0]["category_name"] == "Groceries"


@pytest.mark.asyncio
async def test_advisor_budget_coaching_output(client_and_llm, db):
    import hashlib
    from datetime import date as _date
    from app.models.category import Category as CategoryModel
    from app.models.budget import Budget, BudgetItem

    client, fake = client_and_llm
    user = await _make_user(db, f"adv_budget_{uuid.uuid4().hex[:6]}@test.com")
    acct = FinancialAccount(user_id=user.id, type="bank", name="Checking", currency="USD", balance=Decimal("3000"))
    db.add(acct)
    groceries = CategoryModel(user_id=user.id, name="Groceries", type="expense", rules=[])
    db.add(groceries)
    await db.flush()

    start = _date.today().replace(day=1)
    end = start + timedelta(days=27)
    budget = Budget(
        user_id=user.id, name="Monthly Budget", period_start=start, period_end=end, period_type="monthly", thresholds=[0.8, 0.9, 1.0],
    )
    db.add(budget)
    await db.flush()
    db.add(BudgetItem(budget_id=budget.id, category_id=groceries.id, limit_amount=Decimal("100.00")))
    db.add(Transaction(
        account_id=acct.id, posted_date=start + timedelta(days=4), amount=Decimal("-140.00"),
        description="Groceries", description_normalized="groceries", currency="USD", category_id=groceries.id,
        fingerprint=hashlib.sha256(f"budget_{uuid.uuid4()}".encode()).hexdigest()[:64],
    ))
    await db.commit()

    fake.push(FakeLLM.make_tool_call_response("budget_coaching", {"period": "current_month"}))
    fake.push(FakeLLM.make_text_response("Direct answer: You are over budget.\nInsights:\n- Groceries exceeded limit.\nNext actions:\n- Reduce grocery spend this week."))

    res = await client.post("/api/v1/advisor/message", json={"content": "How can I stay under budget this month?"}, headers=_headers(user.id))
    assert res.status_code == 200
    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    assert len(payload["over_budget"]) >= 1
    assert payload["estimated_amount_to_cut"] > 0


@pytest.mark.asyncio
async def test_advisor_followup_uses_session_date_context(client_and_llm, db):
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_ctx_{uuid.uuid4().hex[:6]}@test.com")
    headers = _headers(user.id)

    fake.push(FakeLLM.make_tool_call_response("get_summary", {
        "date_from": "2026-02-01",
        "date_to": "2026-02-28",
        "expenses_only": True,
    }))
    fake.push(FakeLLM.make_text_response("Direct answer: February summary.\nInsights:\n- ...\nNext actions:\n- ..."))
    first = await client.post("/api/v1/advisor/message", json={"content": "How much did I spend in February 2026?"}, headers=headers)
    assert first.status_code == 200

    fake.push(FakeLLM.make_text_response("Direct answer: Follow-up understood.\nInsights:\n- ...\nNext actions:\n- ..."))
    second = await client.post(
        "/api/v1/advisor/message",
        json={"content": "Compare that to this month", "session_id": first.json()["session_id"]},
        headers=headers,
    )
    assert second.status_code == 200

    last_call_messages = fake.calls[-1]["messages"]
    memory_msgs = [m for m in last_call_messages if m["role"] == "system" and "SESSION MEMORY" in m["content"]]
    assert memory_msgs, "Expected session memory system note for referential follow-up"
    assert "2026-02-01" in memory_msgs[0]["content"]


@pytest.mark.asyncio
async def test_spending_opportunities_excludes_income_and_is_user_scoped(client_and_llm, db):
    import hashlib
    from datetime import date as _date
    from app.models.category import Category as CategoryModel

    client, fake = client_and_llm
    user_a = await _make_user(db, f"adv_opp_a_{uuid.uuid4().hex[:6]}@test.com")
    user_b = await _make_user(db, f"adv_opp_b_{uuid.uuid4().hex[:6]}@test.com")

    acct_a = FinancialAccount(user_id=user_a.id, type="bank", name="A", currency="USD", balance=Decimal("5000"))
    acct_b = FinancialAccount(user_id=user_b.id, type="bank", name="B", currency="USD", balance=Decimal("5000"))
    db.add_all([acct_a, acct_b])
    salary_a = CategoryModel(user_id=user_a.id, name="Salary", type="income", rules=[])
    dining_a = CategoryModel(user_id=user_a.id, name="Dining", type="expense", rules=[])
    dining_b = CategoryModel(user_id=user_b.id, name="Dining", type="expense", rules=[])
    db.add_all([salary_a, dining_a, dining_b])
    await db.flush()

    today = _date.today()
    db.add(Transaction(
        account_id=acct_a.id, posted_date=today - timedelta(days=2), amount=Decimal("5000.00"),
        description="Salary", description_normalized="salary", currency="USD", category_id=salary_a.id,
        fingerprint=hashlib.sha256(f"inc_{uuid.uuid4()}".encode()).hexdigest()[:64],
    ))
    db.add(Transaction(
        account_id=acct_a.id, posted_date=today - timedelta(days=2), amount=Decimal("-400.00"),
        description="Restaurant", description_normalized="restaurant", currency="USD", category_id=dining_a.id,
        fingerprint=hashlib.sha256(f"exp_a_{uuid.uuid4()}".encode()).hexdigest()[:64],
    ))
    db.add(Transaction(
        account_id=acct_b.id, posted_date=today - timedelta(days=2), amount=Decimal("-900.00"),
        description="Restaurant", description_normalized="restaurant", currency="USD", category_id=dining_b.id,
        fingerprint=hashlib.sha256(f"exp_b_{uuid.uuid4()}".encode()).hexdigest()[:64],
    ))
    await db.commit()

    fake.push(FakeLLM.make_tool_call_response("spending_opportunities", {}))
    fake.push(FakeLLM.make_text_response("Direct answer: Dining is an opportunity.\nInsights:\n- Dining spend is elevated.\nNext actions:\n- Reduce restaurant frequency."))
    res = await client.post("/api/v1/advisor/message", json={"content": "Where can I reduce spending first?"}, headers=_headers(user_a.id))
    assert res.status_code == 200

    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user_a.id))
    payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    cats = payload["category_opportunities"]
    assert all(c["current_total"] < 1000 for c in cats), "Must not include other user's spending or income inflow"


@pytest.mark.asyncio
async def test_advisor_explains_blocked_recommendation(client_and_llm, db):
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_explain_blk_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=5000, spending=3000, balance=300)

    from app.services import recommendation_service
    await recommendation_service.execute_run(db, user.id)

    fake.push(FakeLLM.make_tool_call_response("explain_latest_recommendation", {}))
    fake.push(FakeLLM.make_text_response("Direct answer: Recommendation is blocked.\nInsights:\n- Emergency gate failed.\nNext actions:\n- Build emergency fund."))
    res = await client.post("/api/v1/advisor/message", json={"content": "Why is my recommendation blocked?"}, headers=_headers(user.id))
    assert res.status_code == 200
    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    assert payload["blocked"] is True
    assert len(payload.get("unlock_actions", [])) > 0


@pytest.mark.asyncio
async def test_advisor_explains_healthy_recommendation(client_and_llm, db):
    client, fake = client_and_llm
    user = await _make_user(db, f"adv_explain_ok_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=9000, spending=3000, balance=25000)

    from app.services import recommendation_service
    await recommendation_service.execute_run(db, user.id)

    fake.push(FakeLLM.make_tool_call_response("explain_latest_recommendation", {}))
    fake.push(FakeLLM.make_text_response("Direct answer: Balanced is recommended.\nInsights:\n- Gates pass.\nNext actions:\n- Start with recommended tier."))
    res = await client.post("/api/v1/advisor/message", json={"content": "Why did you recommend balanced?"}, headers=_headers(user.id))
    assert res.status_code == 200
    session_res = await client.get(f"/api/v1/advisor/sessions/{res.json()['session_id']}", headers=_headers(user.id))
    payload = [m for m in session_res.json()["messages"] if m["role"] == "tool"][0]["tool_payload"]
    assert payload["blocked"] is False
    assert payload.get("recommended_contribution_monthly") is not None


@pytest.mark.asyncio
async def test_advisor_what_if_larger_amount_improves_projection(db):
    from app.services import recommendation_service
    from app.services.advisor.tool_registry import execute_tool

    user = await _make_user(db, f"adv_sim_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_acct(db, user.id, income=9000, spending=3000, balance=25000)
    await recommendation_service.execute_run(db, user.id)

    low = await execute_tool("simulate_investment_change", db, user.id, {"monthly_amount": 500})
    high = await execute_tool("simulate_investment_change", db, user.id, {"monthly_amount": 1500})
    assert low.get("blocked") is False
    assert high.get("blocked") is False
    assert high["projection_end_override"]["median"] > low["projection_end_override"]["median"]
