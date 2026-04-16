"""UC08: Investment Recommendation Engine tests."""
import uuid
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
from app.services.recommendation_service import (
    compute_risk_score,
    risk_bucket_for_score,
    apply_goal_mode,
    compute_emergency_fund_months,
    compute_investable_amount,
    compute_contribution_tiers,
    rules_gates,
    rules_gates_structured,
    run_projection,
    _validate_allocation_invariant,
    _validate_projection_invariant,
    MODEL_PORTFOLIOS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _risk_profile_payload(**overrides) -> dict:
    base = {
        "risk_profile": {
            "answers": {
                "market_drop_reaction": 3,
                "investment_experience": 2,
                "income_stability": 3,
                "loss_tolerance_pct": 2,
                "goal_priority": 3,
            },
            "horizon_months": 60,
            "liquidity_need": "moderate",
        }
    }
    if overrides:
        base.update(overrides)
    return base


@pytest_asyncio.fixture(scope="function")
async def client(db):
    async def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_account_and_txns(
    db: AsyncSession, user_id: uuid.UUID,
    income: float = 5000.0, spending: float = 3000.0, balance: float = 10000.0,
):
    """Create an account with one income and one spending txn in the last month."""
    from datetime import date, timedelta
    import hashlib

    acct = FinancialAccount(
        user_id=user_id, type="bank", name="Checking", currency="USD", balance=Decimal(str(balance)),
    )
    db.add(acct)
    await db.flush()

    today = date.today()
    recent = today - timedelta(days=15)

    inc_fp = hashlib.sha256(f"income-{user_id}-{uuid.uuid4()}".encode()).hexdigest()[:64]
    db.add(Transaction(
        account_id=acct.id, posted_date=recent, amount=Decimal(str(income)),
        description="Salary", description_normalized="salary",
        fingerprint=inc_fp, currency="USD",
    ))

    exp_fp = hashlib.sha256(f"expense-{user_id}-{uuid.uuid4()}".encode()).hexdigest()[:64]
    db.add(Transaction(
        account_id=acct.id, posted_date=recent, amount=Decimal(str(-spending)),
        description="Expenses", description_normalized="expenses",
        fingerprint=exp_fp, currency="USD",
    ))
    await db.commit()
    return acct


# ---------------------------------------------------------------------------
# Unit tests (pure functions)
# ---------------------------------------------------------------------------

class TestRiskScoring:
    def test_all_min_answers(self):
        answers = {k: 1 for k in [
            "market_drop_reaction", "investment_experience",
            "income_stability", "loss_tolerance_pct", "goal_priority",
        ]}
        assert compute_risk_score(answers) == 0

    def test_all_max_answers(self):
        answers = {k: 5 for k in [
            "market_drop_reaction", "investment_experience",
            "income_stability", "loss_tolerance_pct", "goal_priority",
        ]}
        assert compute_risk_score(answers) == 100

    def test_mid_answers(self):
        answers = {k: 3 for k in [
            "market_drop_reaction", "investment_experience",
            "income_stability", "loss_tolerance_pct", "goal_priority",
        ]}
        score = compute_risk_score(answers)
        assert 40 <= score <= 60

    def test_bucket_boundaries(self):
        assert risk_bucket_for_score(0, 60) == "conservative"
        assert risk_bucket_for_score(30, 60) == "conservative"
        assert risk_bucket_for_score(50, 60) == "balanced"
        assert risk_bucket_for_score(100, 60) == "growth"

    def test_short_horizon_downgrades(self):
        bucket_long = risk_bucket_for_score(50, 120)
        bucket_short = risk_bucket_for_score(50, 12)
        buckets_order = ["conservative", "moderate_conservative", "balanced", "moderate_growth", "growth"]
        assert buckets_order.index(bucket_short) <= buckets_order.index(bucket_long)

    def test_goal_mode_adjusts_bucket(self):
        adjusted, _ = apply_goal_mode("balanced", "emergency", 12)
        assert adjusted in {"conservative", "moderate_conservative"}
        adjusted_ret, _ = apply_goal_mode("balanced", "retirement", 240)
        assert adjusted_ret in {"balanced", "moderate_growth", "growth"}


class TestComputations:
    def test_emergency_months_zero_spending(self):
        assert compute_emergency_fund_months(Decimal("5000"), Decimal("0")) == 99.0

    def test_emergency_months_normal(self):
        result = compute_emergency_fund_months(Decimal("6000"), Decimal("2000"))
        assert result == 3.0

    def test_investable_negative_cashflow(self):
        assert compute_investable_amount(Decimal("2000"), Decimal("3000")) == 0.0

    def test_investable_positive(self):
        result = compute_investable_amount(Decimal("5000"), Decimal("3000"))
        assert result == 1600.0  # (5000-3000) * 0.80

    def test_contribution_tiers_ordering(self):
        tiers = compute_contribution_tiers(Decimal("8000"), Decimal("3000"), 4000.0)
        assert tiers is not None
        assert tiers["safe_contribution_monthly"] <= tiers["recommended_contribution_monthly"] <= tiers["stretch_contribution_monthly"]

    def test_rules_gates_low_emergency(self):
        warnings = rules_gates(0.5, True, 0)
        assert len(warnings) == 1
        assert "emergency" in warnings[0].lower()

    def test_rules_gates_negative_cashflow(self):
        warnings = rules_gates(6.0, False, 0)
        assert len(warnings) == 1
        assert "spending exceeds" in warnings[0].lower()

    def test_rules_gates_clear(self):
        warnings = rules_gates(6.0, True, 0)
        assert len(warnings) == 0


class TestProjection:
    def test_deterministic_same_seed(self):
        p1 = run_projection(500, 1000, 24, 0.07, 0.12, 42)
        p2 = run_projection(500, 1000, 24, 0.07, 0.12, 42)
        assert p1 == p2

    def test_different_seed_differs(self):
        p1 = run_projection(500, 1000, 24, 0.07, 0.12, 42)
        p2 = run_projection(500, 1000, 24, 0.07, 0.12, 99)
        assert p1 != p2

    def test_projection_increases_with_contribution(self):
        p_low = run_projection(100, 0, 60, 0.07, 0.12, 42)
        p_high = run_projection(1000, 0, 60, 0.07, 0.12, 42)
        last_low = p_low[-1]["median"]
        last_high = p_high[-1]["median"]
        assert last_high > last_low


# ---------------------------------------------------------------------------
# Integration tests (HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_user_isolation(client: AsyncClient, db: AsyncSession):
    user_a = await _make_user(db, f"rec_a_{uuid.uuid4().hex[:6]}@test.com")
    user_b = await _make_user(db, f"rec_b_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user_a.id)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user_a.id))
    assert res.status_code == 201
    run_id = res.json()["id"]

    get_b = await client.get(f"/api/v1/recommendations/runs/{run_id}", headers=_headers(user_b.id))
    assert get_b.status_code == 404


@pytest.mark.asyncio
async def test_fund_below_one_month_suppresses_investing(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_lowfund_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=5000, spending=3000, balance=500)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    data = res.json()

    outputs = data["outputs"]
    assert outputs["investable_monthly"] == 0.0
    assert len(outputs["safety_warnings"]) > 0
    assert outputs["allocation"] == []

    item_types = [i["type"] for i in data["items"]]
    assert "invest" not in item_types
    assert "emergency_fund" in item_types


@pytest.mark.asyncio
async def test_negative_cashflow_zero_investable(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_negcf_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=1000, spending=3000, balance=20000)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    outputs = res.json()["outputs"]
    assert outputs["investable_monthly"] == 0.0
    assert not outputs["cashflow_positive"]
    assert any("spending exceeds" in w.lower() for w in outputs["safety_warnings"])


@pytest.mark.asyncio
async def test_healthy_user_gets_allocation(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_healthy_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=8000, spending=3000, balance=20000)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    data = res.json()
    outputs = data["outputs"]
    assert outputs["investable_monthly"] > 0
    assert outputs["cashflow_positive"]
    assert len(outputs["safety_warnings"]) == 0
    assert len(outputs["allocation"]) > 0
    assert sum(a["pct"] for a in outputs["allocation"]) == 100
    assert len(outputs["projection"]) > 0

    item_types = [i["type"] for i in data["items"]]
    assert "invest" in item_types


@pytest.mark.asyncio
async def test_list_ordering_newest_first(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_list_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id)
    headers = _headers(user.id)

    ids = []
    for _ in range(3):
        r = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=headers)
        assert r.status_code == 201
        ids.append(r.json()["id"])

    res = await client.get("/api/v1/recommendations/runs", headers=headers)
    assert res.status_code == 200
    listed = [r["id"] for r in res.json()]
    assert listed[0] == ids[-1]
    assert listed[-1] == ids[0]


@pytest.mark.asyncio
async def test_invalid_horizon_validation(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_val_{uuid.uuid4().hex[:6]}@test.com")
    headers = _headers(user.id)

    payload = _risk_profile_payload()
    payload["risk_profile"]["horizon_months"] = 2
    res = await client.post("/api/v1/recommendations/run", json=payload, headers=headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_no_risk_profile_sets_needs_profile(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_noprof_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id)

    res = await client.post("/api/v1/recommendations/run", json={}, headers=_headers(user.id))
    assert res.status_code == 201
    assert res.json()["outputs"]["needs_profile"] is True


@pytest.mark.asyncio
async def test_risk_score_bucket_mapping(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_bucket_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=10000, spending=2000, balance=50000)
    headers = _headers(user.id)

    payload_conservative = _risk_profile_payload()
    payload_conservative["risk_profile"]["answers"] = {
        "market_drop_reaction": 1, "investment_experience": 1,
        "income_stability": 1, "loss_tolerance_pct": 1, "goal_priority": 1,
    }
    r1 = await client.post("/api/v1/recommendations/run", json=payload_conservative, headers=headers)
    assert r1.json()["outputs"]["risk_bucket"] == "conservative"

    payload_growth = _risk_profile_payload()
    payload_growth["risk_profile"]["answers"] = {
        "market_drop_reaction": 5, "investment_experience": 5,
        "income_stability": 5, "loss_tolerance_pct": 5, "goal_priority": 5,
    }
    r2 = await client.post("/api/v1/recommendations/run", json=payload_growth, headers=headers)
    assert r2.json()["outputs"]["risk_bucket"] == "growth"


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_allocation_sum_valid(self):
        for bucket, alloc in MODEL_PORTFOLIOS.items():
            _validate_allocation_invariant(alloc)

    def test_allocation_sum_invalid_raises(self):
        bad = [{"ticker": "X", "pct": 60}, {"ticker": "Y", "pct": 30}]
        with pytest.raises(RuntimeError, match="INVARIANT VIOLATION"):
            _validate_allocation_invariant(bad)

    def test_allocation_empty_passes(self):
        _validate_allocation_invariant([])

    def test_projection_ordering_valid(self):
        proj = run_projection(500, 1000, 24, 0.07, 0.12, 42)
        _validate_projection_invariant(proj)

    def test_projection_ordering_invalid_raises(self):
        bad = [{"month": 1, "p10": 200, "median": 100, "p90": 300}]
        with pytest.raises(RuntimeError, match="INVARIANT VIOLATION"):
            _validate_projection_invariant(bad)

    def test_all_model_portfolios_sum_100(self):
        for bucket, alloc in MODEL_PORTFOLIOS.items():
            total = sum(a["pct"] for a in alloc)
            assert total == 100, f"{bucket} sums to {total}"


class TestStructuredGates:
    def test_all_gates_pass(self):
        gates = rules_gates_structured(6.0, True, 0)
        assert all(g["passed"] for g in gates)
        assert len(gates) == 3

    def test_emergency_gate_fails(self):
        gates = rules_gates_structured(0.5, True, 0)
        ef_gate = next(g for g in gates if g["code"] == "EMERGENCY_FUND")
        assert not ef_gate["passed"]

    def test_cashflow_gate_fails(self):
        gates = rules_gates_structured(6.0, False, 0)
        cf_gate = next(g for g in gates if g["code"] == "POSITIVE_CASHFLOW")
        assert not cf_gate["passed"]

    def test_budget_gate_fails(self):
        gates = rules_gates_structured(6.0, True, 3)
        bg_gate = next(g for g in gates if g["code"] == "BUDGET_HEALTH")
        assert not bg_gate["passed"]


# ---------------------------------------------------------------------------
# Extended output fields integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outputs_contain_gates_and_risk(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_ext_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=8000, spending=3000, balance=20000)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    out = res.json()["outputs"]

    assert "gates" in out
    assert len(out["gates"]) == 3
    for g in out["gates"]:
        assert "code" in g and "passed" in g and "reason" in g

    assert "risk" in out
    assert out["risk"]["score"] is not None
    assert out["risk"]["bucket"] is not None
    assert "horizon_adjustment" in out["risk"]

    assert "assumptions" in out
    assert out["assumptions"]["paths"] == 500
    assert out["assumptions"]["step"] == "monthly"

    assert "allocation_rationale" in out
    if out["allocation"]:
        assert len(out["allocation_rationale"]) == len(out["allocation"])


@pytest.mark.asyncio
async def test_blocked_run_gates_show_failures(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_blkgate_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=5000, spending=3000, balance=500)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    out = res.json()["outputs"]

    ef_gate = next(g for g in out["gates"] if g["code"] == "EMERGENCY_FUND")
    assert not ef_gate["passed"]
    assert out["allocation"] == []
    assert out["allocation_rationale"] == []
    assert out["safe_contribution_monthly"] is None
    assert out["recommended_contribution_monthly"] is None
    assert out["stretch_contribution_monthly"] is None


@pytest.mark.asyncio
async def test_explanation_fields_present(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_explain_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=8000, spending=3000, balance=20000)

    res = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=_headers(user.id))
    assert res.status_code == 201
    out = res.json()["outputs"]

    assert out["why_this_bucket"]
    assert out["why_now_or_not_now"]
    assert out["downside_note"]
    assert out["rebalance_guidance"]
    assert "unlock_actions" in out


@pytest.mark.asyncio
async def test_goal_type_affects_bucket_direction(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_goal_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=10000, spending=3000, balance=25000)
    headers = _headers(user.id)

    base_payload = _risk_profile_payload()
    base_payload["risk_profile"]["answers"] = {
        "market_drop_reaction": 3,
        "investment_experience": 3,
        "income_stability": 3,
        "loss_tolerance_pct": 3,
        "goal_priority": 3,
    }
    base_payload["horizon_months"] = 120

    emergency = await client.post(
        "/api/v1/recommendations/run",
        json={**base_payload, "goal_type": "emergency", "target_horizon_months": 12},
        headers=headers,
    )
    retirement = await client.post(
        "/api/v1/recommendations/run",
        json={**base_payload, "goal_type": "retirement", "target_horizon_months": 240},
        headers=headers,
    )
    assert emergency.status_code == 201
    assert retirement.status_code == 201

    order = ["conservative", "moderate_conservative", "balanced", "moderate_growth", "growth"]
    b_em = emergency.json()["outputs"]["risk_bucket"]
    b_ret = retirement.json()["outputs"]["risk_bucket"]
    assert order.index(b_em) <= order.index(b_ret)


@pytest.mark.asyncio
async def test_what_if_higher_contribution_improves_median(client: AsyncClient, db: AsyncSession):
    user = await _make_user(db, f"rec_whatif_{uuid.uuid4().hex[:6]}@test.com")
    await _seed_account_and_txns(db, user.id, income=9000, spending=3000, balance=20000)
    headers = _headers(user.id)

    run = await client.post("/api/v1/recommendations/run", json=_risk_profile_payload(), headers=headers)
    assert run.status_code == 201

    low = await client.post("/api/v1/recommendations/what-if", json={"monthly_amount": 800}, headers=headers)
    high = await client.post("/api/v1/recommendations/what-if", json={"monthly_amount": 1800}, headers=headers)
    assert low.status_code == 200
    assert high.status_code == 200

    low_end = low.json()["projection_end_override"]["median"]
    high_end = high.json()["projection_end_override"]["median"]
    assert high_end > low_end
