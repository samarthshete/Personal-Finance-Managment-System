"""
BudgetFlowApp Demo Data Seeder
==============================

Usage
-----
From BudgetFlowApp/ (repo root, DB must be running):

    make seed-demo

Or manually from the backend/ directory:

    python -m app.scripts.seed_demo_data

Demo Credentials (all use the same password)
---------------------------------------------
    healthy@example.com   / DemoPass123!
      → 3 accounts (BofA Checking + Amex + Fidelity), 3 months of transactions
        distributed across accounts, budgets under control, recommendation with
        allocation & projection, advisor chat, one seeded import session + job.

    stressed@example.com  / DemoPass123!
      → 1 account, overspending, 2 severe budget alerts (unread),
        recommendation blocked by safety gates, advisor chat with warnings.

    newuser@example.com   / DemoPass123!
      → 1 account, minimal transactions, 1 simple budget, no chat/recs.

The script is IDEMPOTENT: existing demo users are deleted (cascade) and
recreated on every run. Shared global rows (institutions, merchants) are
upserted by name and never duplicated.
"""

import asyncio
import calendar
import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.account import (
    BankAccount,
    CreditCardAccount,
    FinancialAccount,
    InvestmentAccount,
    Institution,
)
from app.models.alert import BudgetAlert
from app.models.budget import Budget, BudgetItem
from app.models.category import Category
from app.models.chat import ChatMessage, ChatSession
from app.models.import_session import ImportSession
from app.models.job import Job
from app.models.recommendation import (
    RecommendationItem,
    RecommendationRun,
    RiskProfile,
)
from app.models.report import Report
from app.models.transaction import Merchant, Transaction
from app.models.user import User
from app.services.recommendation_service import (
    BUFFER_FACTOR,
    EMERGENCY_TARGET_MONTHS,
    INFLATION_RATE,
    MODEL_PORTFOLIOS,
    RISK_BUCKETS,
    SIM_PATHS,
    _build_action_items,
    compute_emergency_fund_months,
    compute_investable_amount,
    compute_risk_score,
    risk_bucket_for_score,
    rules_gates,
    rules_gates_structured,
    run_projection,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMO_PASSWORD = "DemoPass123!"
TODAY = date.today()
NOW_UTC = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def _at(days_ago: int, hour: int = 10) -> datetime:
    return NOW_UTC - timedelta(days=days_ago) + timedelta(hours=hour - 10)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _fingerprint(posted_date: date, amount: Decimal, desc_normalized: str) -> str:
    raw = f"{posted_date.isoformat()}|{amount}|{desc_normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _current_month_range():
    last_day = calendar.monthrange(TODAY.year, TODAY.month)[1]
    return date(TODAY.year, TODAY.month, 1), date(TODAY.year, TODAY.month, last_day)


async def _upsert_institution(db: AsyncSession, name: str) -> Institution:
    result = await db.execute(select(Institution).where(Institution.name == name))
    inst = result.scalars().first()
    if not inst:
        inst = Institution(name=name)
        db.add(inst)
        await db.flush()
    return inst


async def _upsert_merchant(db: AsyncSession, name: str) -> Merchant:
    norm = _normalize(name)
    result = await db.execute(select(Merchant).where(Merchant.name_normalized == norm))
    m = result.scalars().first()
    if not m:
        m = Merchant(name=name, name_normalized=norm)
        db.add(m)
        await db.flush()
    return m


async def _delete_demo_user(db: AsyncSession, email: str) -> None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user:
        # Delete in explicit dependency order to avoid FK-ordering edge cases
        # across mixed cascades/restrict constraints in existing schema.
        budget_ids = select(Budget.id).where(Budget.user_id == user.id)
        rec_run_ids = select(RecommendationRun.id).where(RecommendationRun.user_id == user.id)
        chat_session_ids = select(ChatSession.id).where(ChatSession.user_id == user.id)
        account_ids = select(FinancialAccount.id).where(FinancialAccount.user_id == user.id)

        await db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(chat_session_ids)))
        await db.execute(delete(ChatSession).where(ChatSession.user_id == user.id))

        await db.execute(delete(RecommendationItem).where(RecommendationItem.run_id.in_(rec_run_ids)))
        await db.execute(delete(RecommendationRun).where(RecommendationRun.user_id == user.id))
        await db.execute(delete(RiskProfile).where(RiskProfile.user_id == user.id))

        await db.execute(delete(BudgetAlert).where(BudgetAlert.user_id == user.id))
        await db.execute(delete(BudgetItem).where(BudgetItem.budget_id.in_(budget_ids)))
        await db.execute(delete(Budget).where(Budget.user_id == user.id))

        await db.execute(delete(Transaction).where(Transaction.account_id.in_(account_ids)))
        await db.execute(delete(ImportSession).where(ImportSession.user_id == user.id))
        await db.execute(delete(Job).where(Job.user_id == user.id))
        await db.execute(delete(Report).where(Report.user_id == user.id))
        await db.execute(delete(FinancialAccount).where(FinancialAccount.user_id == user.id))
        await db.execute(delete(Category).where(Category.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.flush()


def _txn(
    account_id: uuid.UUID,
    posted_date: date,
    amount: Decimal,
    description: str,
    merchant_id: Optional[uuid.UUID],
    category_id: Optional[uuid.UUID],
    currency: str = "USD",
) -> Transaction:
    desc_norm = _normalize(description)
    fp = _fingerprint(posted_date, amount, desc_norm)
    return Transaction(
        account_id=account_id,
        posted_date=posted_date,
        amount=amount,
        description=description,
        description_normalized=desc_norm,
        currency=currency,
        merchant_id=merchant_id,
        category_id=category_id,
        fingerprint=fp,
        categorization_source="rule" if category_id else None,
        category_confidence=Decimal("1.000") if category_id else None,
        needs_manual=False,
    )


def _rec_outputs(
    monthly_income: Decimal,
    monthly_spending: Decimal,
    balance: Decimal,
    answers: dict,
    horizon: int,
    liquidity: str,
    severe_alerts: int,
    run_seed: int = 42,
) -> tuple[dict, list[dict]]:
    """Compute recommendation outputs. Returns (outputs_dict, action_items)."""
    score = compute_risk_score(answers)
    bucket = risk_bucket_for_score(score, horizon)
    bucket_cfg = RISK_BUCKETS[bucket]

    ef_months = compute_emergency_fund_months(balance, monthly_spending)
    cashflow_positive = float(monthly_income) >= float(monthly_spending)
    investable = compute_investable_amount(monthly_income, monthly_spending)

    warnings = rules_gates(ef_months, cashflow_positive, severe_alerts)
    gates = rules_gates_structured(ef_months, cashflow_positive, severe_alerts)

    if warnings:
        investable = 0.0

    allocation = MODEL_PORTFOLIOS[bucket] if not warnings else []
    allocation_rationale = [
        f"{a['ticker']} ({a['pct']}%): {a['rationale']}" for a in allocation
    ]

    projection: list[dict] = []
    if not warnings and investable > 0:
        projection = run_projection(
            monthly_contribution=investable,
            initial_balance=float(balance),
            horizon_months=horizon,
            annual_return=bucket_cfg["return_pct"],
            annual_vol=bucket_cfg["vol_pct"],
            run_seed=run_seed,
        )

    horizon_adj = 0
    if horizon < 24:
        horizon_adj = -15
    elif horizon < 36:
        horizon_adj = -5
    elif horizon > 120:
        horizon_adj = 5

    outputs = {
        "needs_profile": False,
        "risk_bucket": bucket,
        "risk_score": score,
        "monthly_spending_avg": round(float(monthly_spending), 2),
        "emergency_fund_months": ef_months,
        "investable_monthly": investable,
        "cashflow_positive": cashflow_positive,
        "safety_warnings": warnings,
        "allocation": allocation,
        "projection": projection,
        "gates": gates,
        "risk": {"score": score, "bucket": bucket, "horizon_adjustment": horizon_adj},
        "allocation_rationale": allocation_rationale,
        "assumptions": {
            "expected_return": bucket_cfg["return_pct"],
            "volatility": bucket_cfg["vol_pct"],
            "paths": SIM_PATHS,
            "step": "monthly",
            "inflation_assumed": INFLATION_RATE,
            "buffer_factor": BUFFER_FACTOR,
        },
    }

    action_items = _build_action_items(
        warnings, ef_months, cashflow_positive, investable, bucket, allocation,
    )
    return outputs, action_items


# ---------------------------------------------------------------------------
# A) healthy@example.com
#
# Account distribution (realistic):
#   BofA Checking → salary, rent, groceries, insurance, utilities
#   Amex Blue Cash → coffee, shopping, entertainment, transport   (typical credit-card use)
#   Fidelity Brokerage → investment holdings only, no transactions
# ---------------------------------------------------------------------------

async def seed_healthy(db: AsyncSession) -> dict:
    user = User(
        email="healthy@example.com",
        name="Alex Morgan",
        hashed_password=get_password_hash(DEMO_PASSWORD),
    )
    db.add(user)
    await db.flush()

    # ── Institutions ──────────────────────────────────────────────────────
    bofa     = await _upsert_institution(db, "Bank of America")
    amex_i   = await _upsert_institution(db, "American Express")
    fidelity = await _upsert_institution(db, "Fidelity Investments")

    # ── Accounts ──────────────────────────────────────────────────────────
    checking = BankAccount(
        user_id=user.id, institution_id=bofa.id,
        name="BofA Checking", currency="USD",
        balance=Decimal("12_450.00"),
        bank_account_number_last4="4821",
    )
    credit = CreditCardAccount(
        user_id=user.id, institution_id=amex_i.id,
        name="Amex Blue Cash", currency="USD",
        balance=Decimal("-1_240.50"),
        credit_card_last4="3719",
        credit_limit=10_000.0,
    )
    investment = InvestmentAccount(
        user_id=user.id, institution_id=fidelity.id,
        name="Fidelity Brokerage", currency="USD",
        balance=Decimal("34_800.00"),
        broker_name="Fidelity Investments",
    )
    db.add_all([checking, credit, investment])
    await db.flush()

    # ── Categories (user-scoped) ───────────────────────────────────────────
    cat_defs = [
        ("Salary",        "income",  []),
        ("Groceries",     "expense", [{"pattern": "whole foods", "match": "contains", "priority": 10}]),
        ("Coffee",        "expense", [{"pattern": "starbucks",   "match": "contains", "priority": 10}]),
        ("Transport",     "expense", [{"pattern": "uber",        "match": "contains", "priority": 10}]),
        ("Rent",          "expense", []),
        ("Insurance",     "expense", [{"pattern": "geico",       "match": "contains", "priority": 10}]),
        ("Shopping",      "expense", [
            {"pattern": "amazon", "match": "contains", "priority": 8},
            {"pattern": "target", "match": "contains", "priority": 8},
        ]),
        ("Entertainment", "expense", [
            {"pattern": "netflix",  "match": "contains", "priority": 10},
            {"pattern": "spotify",  "match": "contains", "priority": 10},
        ]),
        ("Utilities",     "expense", []),
    ]
    cats: dict[str, Category] = {}
    for name, ctype, rules in cat_defs:
        c = Category(user_id=user.id, name=name, type=ctype, rules=rules)
        db.add(c)
        cats[name] = c
    await db.flush()

    # ── Merchants (global, upserted) ──────────────────────────────────────
    whole_foods = await _upsert_merchant(db, "Whole Foods")
    starbucks   = await _upsert_merchant(db, "Starbucks")
    uber        = await _upsert_merchant(db, "Uber")
    amazon      = await _upsert_merchant(db, "Amazon")
    target      = await _upsert_merchant(db, "Target")
    netflix     = await _upsert_merchant(db, "Netflix")
    spotify     = await _upsert_merchant(db, "Spotify")
    geico       = await _upsert_merchant(db, "GEICO")
    techcorp    = await _upsert_merchant(db, "Techcorp Inc")

    chk = checking.id   # BofA Checking
    amx = credit.id     # Amex Blue Cash

    txns: list[Transaction] = []

    # ── Month 1 — 65 days ago ─────────────────────────────────────────────
    # BofA: salary, rent, groceries, insurance, utilities
    M1 = 65
    txns += [
        _txn(chk, _ago(M1),    Decimal("8_200.00"),  "Techcorp Inc Salary",   techcorp.id,    cats["Salary"].id),
        _txn(chk, _ago(M1-2),  Decimal("-2_000.00"), "Monthly Rent Payment",  None,           cats["Rent"].id),
        _txn(chk, _ago(M1-4),  Decimal("-248.30"),   "Whole Foods Market",    whole_foods.id, cats["Groceries"].id),
        _txn(chk, _ago(M1-12), Decimal("-178.40"),   "Whole Foods Market",    whole_foods.id, cats["Groceries"].id),
        _txn(chk, _ago(M1-16), Decimal("-142.00"),   "GEICO Auto Insurance",  geico.id,       cats["Insurance"].id),
        _txn(chk, _ago(M1-22), Decimal("-125.00"),   "ConEd Electric Bill",   None,           cats["Utilities"].id),
    ]
    # Amex: coffee, shopping, entertainment, transport
    txns += [
        _txn(amx, _ago(M1-5),  Decimal("-6.50"),     "Starbucks",             starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M1-7),  Decimal("-34.20"),    "Uber Trip",             uber.id,        cats["Transport"].id),
        _txn(amx, _ago(M1-8),  Decimal("-7.00"),     "Starbucks Coffee",      starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M1-10), Decimal("-89.99"),    "Amazon Purchase",       amazon.id,      cats["Shopping"].id),
        _txn(amx, _ago(M1-14), Decimal("-15.99"),    "Netflix Subscription",  netflix.id,     cats["Entertainment"].id),
        _txn(amx, _ago(M1-18), Decimal("-5.80"),     "Starbucks Morning",     starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M1-20), Decimal("-112.50"),   "Target Store",          target.id,      cats["Shopping"].id),
    ]

    # ── Month 2 — 35 days ago ─────────────────────────────────────────────
    M2 = 35
    txns += [
        _txn(chk, _ago(M2),    Decimal("8_200.00"),  "Techcorp Inc Salary",   techcorp.id,    cats["Salary"].id),
        _txn(chk, _ago(M2-2),  Decimal("-2_000.00"), "Monthly Rent Payment",  None,           cats["Rent"].id),
        _txn(chk, _ago(M2-4),  Decimal("-212.60"),   "Whole Foods Market",    whole_foods.id, cats["Groceries"].id),
        _txn(chk, _ago(M2-12), Decimal("-189.30"),   "Whole Foods Market",    whole_foods.id, cats["Groceries"].id),
        _txn(chk, _ago(M2-16), Decimal("-142.00"),   "GEICO Auto Insurance",  geico.id,       cats["Insurance"].id),
        _txn(chk, _ago(M2-24), Decimal("-118.00"),   "ConEd Electric Bill",   None,           cats["Utilities"].id),
    ]
    txns += [
        _txn(amx, _ago(M2-5),  Decimal("-6.80"),     "Starbucks",             starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M2-7),  Decimal("-28.50"),    "Uber Trip",             uber.id,        cats["Transport"].id),
        _txn(amx, _ago(M2-8),  Decimal("-5.50"),     "Starbucks Coffee",      starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M2-10), Decimal("-145.00"),   "Amazon Purchase",       amazon.id,      cats["Shopping"].id),
        _txn(amx, _ago(M2-14), Decimal("-15.99"),    "Netflix Subscription",  netflix.id,     cats["Entertainment"].id),
        _txn(amx, _ago(M2-18), Decimal("-7.20"),     "Starbucks Morning",     starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(M2-20), Decimal("-95.00"),    "Target Store",          target.id,      cats["Shopping"].id),
        _txn(amx, _ago(M2-22), Decimal("-9.99"),     "Spotify Premium",       spotify.id,     cats["Entertainment"].id),
    ]

    # ── Current month — last 14 days ──────────────────────────────────────
    txns += [
        _txn(chk, _ago(14), Decimal("8_200.00"),  "Techcorp Inc Salary",  techcorp.id,    cats["Salary"].id),
        _txn(chk, _ago(12), Decimal("-2_000.00"), "Monthly Rent Payment", None,           cats["Rent"].id),
        _txn(chk, _ago(10), Decimal("-198.50"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(chk, _ago(2),  Decimal("-142.00"),   "GEICO Auto Insurance", geico.id,       cats["Insurance"].id),
    ]
    txns += [
        _txn(amx, _ago(8),  Decimal("-6.50"),     "Starbucks",            starbucks.id,   cats["Coffee"].id),
        _txn(amx, _ago(6),  Decimal("-22.00"),    "Uber Trip",            uber.id,        cats["Transport"].id),
        _txn(amx, _ago(4),  Decimal("-75.00"),    "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
    ]

    db.add_all(txns)
    await db.flush()

    # Count by account for reporting
    chk_txn_count = sum(1 for t in txns if t.account_id == chk)
    amx_txn_count = sum(1 for t in txns if t.account_id == amx)

    # ── Budget (current month, mostly under threshold) ─────────────────────
    month_start, month_end = _current_month_range()
    budget = Budget(
        user_id=user.id,
        name="Monthly Budget",
        period_start=month_start,
        period_end=month_end,
        period_type="monthly",
        thresholds=[0.8, 0.9, 1.0],
    )
    db.add(budget)
    await db.flush()

    db.add_all([
        BudgetItem(budget_id=budget.id, category_id=cats["Groceries"].id,     limit_amount=Decimal("450.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Coffee"].id,        limit_amount=Decimal("80.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Transport"].id,     limit_amount=Decimal("100.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Shopping"].id,      limit_amount=Decimal("250.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Entertainment"].id, limit_amount=Decimal("120.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Utilities"].id,     limit_amount=Decimal("150.00")),
    ])
    await db.flush()

    # ── Alert — one 80 % warning (already read, non-blocking) ─────────────
    db.add(BudgetAlert(
        user_id=user.id,
        budget_id=budget.id,
        category_id=cats["Groceries"].id,
        threshold_percent=Decimal("0.800"),
        spent_amount=Decimal("198.50"),
        limit_amount=Decimal("450.00"),
        period_start=month_start,
        period_end=month_end,
        is_read=True,
    ))
    await db.flush()

    # ── Report (seeded as succeeded; storage_key mimics real path) ────────
    report_completed_at = NOW_UTC - timedelta(hours=2)
    report = Report(
        user_id=user.id,
        type="monthly_summary",
        format="csv",
        from_date=month_start,
        to_date=month_end,
        status="succeeded",
        storage_key=f"reports/{user.id}/monthly_summary_demo.csv",
        completed_at=report_completed_at,
    )
    db.add(report)
    await db.flush()

    # ── Seeded import session + job (represents a past CSV import of BofA txns)
    import_started = NOW_UTC - timedelta(days=65, hours=2)
    import_finished = import_started + timedelta(seconds=42)
    import_session = ImportSession(
        user_id=user.id,
        account_id=checking.id,
        status="completed",
        total_rows=chk_txn_count,
        imported_count=chk_txn_count,
        duplicate_count=0,
        failed_count=0,
        started_at=import_started,
        completed_at=import_finished,
        metadata_json={"filename": "bofa_checking_history.csv"},
    )
    db.add(import_session)
    await db.flush()

    import_job = Job(
        user_id=user.id,
        type="transactions.import_csv",
        status="succeeded",
        payload={
            "user_id": str(user.id),
            "account_id": str(checking.id),
            "import_session_id": str(import_session.id),
            "filename": "bofa_checking_history.csv",
        },
        result={
            "import_session_id": str(import_session.id),
            "inserted_count": chk_txn_count,
            "skipped_duplicates": 0,
            "failed_rows": 0,
            "row_errors": [],
        },
        started_at=import_started,
        finished_at=import_finished,
    )
    db.add(import_job)
    await db.flush()

    # ── Recommendation run (healthy: all gates pass) ───────────────────────
    # Monthly spending ≈ expense txns over 90 days / 3.
    # Salary is positive → excluded from spending avg.
    h_answers = {
        "market_drop_reaction": 3,
        "investment_experience": 3,
        "income_stability": 4,
        "loss_tolerance_pct": 3,
        "goal_priority": 4,
    }
    outputs_h, action_items_h = _rec_outputs(
        monthly_income=Decimal("8_200.00"),
        monthly_spending=Decimal("2_810.00"),   # ~expenses only
        balance=Decimal("12_450.00"),           # checking balance (emergency fund)
        answers=h_answers,
        horizon=120,
        liquidity="moderate",
        severe_alerts=0,
        run_seed=1001,
    )
    db.add(RiskProfile(
        user_id=user.id,
        score=compute_risk_score(h_answers),
        horizon_months=120,
        liquidity_need="moderate",
        answers_json=h_answers,
    ))
    rec_run = RecommendationRun(
        user_id=user.id,
        status="completed",
        inputs_snapshot={
            "score": outputs_h["risk_score"],
            "horizon_months": 120,
            "liquidity_need": "moderate",
            "needs_profile": False,
        },
        outputs=outputs_h,
    )
    db.add(rec_run)
    await db.flush()
    for item_data in action_items_h:
        db.add(RecommendationItem(
            run_id=rec_run.id,
            priority=item_data["priority"],
            type=item_data["type"],
            title=item_data["title"],
            details=item_data.get("details"),
            confidence=Decimal(str(item_data["confidence"])),
        ))
    await db.flush()

    # ── Advisor chat ──────────────────────────────────────────────────────
    investable_str = f"${outputs_h['investable_monthly']:,.0f}"
    session = ChatSession(user_id=user.id, title="Investment strategy check")
    db.add(session)
    await db.flush()
    db.add_all([
        ChatMessage(session_id=session.id, role="user", created_at=_at(1, 14),
            content="What's the best way to start investing given my current situation?"),
        ChatMessage(session_id=session.id, role="assistant", created_at=_at(1, 14),
            content=(
                f"Based on your profile you're in great shape to start investing. "
                f"You have positive cash flow, a solid emergency fund (~4.4 months), "
                f"and consistent $8,200/month income. I recommend a "
                f"{outputs_h['risk_bucket'].replace('_', ' ')} ETF portfolio. "
                f"You could comfortably invest around {investable_str}/month. "
                "Would you like a breakdown of the specific allocation?"
            )),
        ChatMessage(session_id=session.id, role="user", created_at=_at(1, 14),
            content="Yes — how should I split it and where do I open an account?"),
        ChatMessage(session_id=session.id, role="assistant", created_at=_at(1, 14),
            content=(
                "Your balanced allocation: 35% VTI (US Large Cap), 20% VXUS (International), "
                "25% AGG (US Bonds), 10% VNQ (Real Estate), 10% TIP (Inflation). "
                "Open a brokerage at Fidelity, Vanguard, or Schwab — all offer these ETFs "
                "commission-free. Set up automatic monthly contributions and rebalance annually. "
                "Your 10-year median projection shows strong portfolio growth at current rates."
            )),
    ])
    await db.flush()

    return {
        "accounts": 3,
        "transactions": len(txns),
        "transactions_on_checking": chk_txn_count,
        "transactions_on_amex": amx_txn_count,
        "budgets": True,
        "alerts": True,
        "recommendations": True,
        "reports": True,
        "import_session": True,
        "chat": True,
    }


# ---------------------------------------------------------------------------
# B) stressed@example.com
# ---------------------------------------------------------------------------

async def seed_stressed(db: AsyncSession) -> dict:
    user = User(
        email="stressed@example.com",
        name="Jordan Lee",
        hashed_password=get_password_hash(DEMO_PASSWORD),
    )
    db.add(user)
    await db.flush()

    chase = await _upsert_institution(db, "Chase Bank")
    checking = BankAccount(
        user_id=user.id, institution_id=chase.id,
        name="Chase Checking", currency="USD",
        balance=Decimal("850.00"),
        bank_account_number_last4="9342",
    )
    db.add(checking)
    await db.flush()

    cat_defs = [
        ("Salary",        "income",  []),
        ("Groceries",     "expense", [{"pattern": "whole foods", "match": "contains", "priority": 10}]),
        ("Coffee",        "expense", [{"pattern": "starbucks",   "match": "contains", "priority": 10}]),
        ("Transport",     "expense", [{"pattern": "uber",        "match": "contains", "priority": 10}]),
        ("Rent",          "expense", []),
        ("Shopping",      "expense", [
            {"pattern": "amazon", "match": "contains", "priority": 8},
            {"pattern": "target", "match": "contains", "priority": 8},
        ]),
        ("Entertainment", "expense", [{"pattern": "netflix", "match": "contains", "priority": 10}]),
        ("Dining Out",    "expense", [{"pattern": "doordash", "match": "contains", "priority": 10}]),
    ]
    cats: dict[str, Category] = {}
    for name, ctype, rules in cat_defs:
        c = Category(user_id=user.id, name=name, type=ctype, rules=rules)
        db.add(c)
        cats[name] = c
    await db.flush()

    whole_foods = await _upsert_merchant(db, "Whole Foods")
    starbucks   = await _upsert_merchant(db, "Starbucks")
    uber        = await _upsert_merchant(db, "Uber")
    amazon      = await _upsert_merchant(db, "Amazon")
    target      = await _upsert_merchant(db, "Target")
    netflix     = await _upsert_merchant(db, "Netflix")
    doordash    = await _upsert_merchant(db, "DoorDash")
    retailco    = await _upsert_merchant(db, "RetailCo Inc")

    acct = checking.id
    txns: list[Transaction] = []

    M1 = 65
    txns += [
        _txn(acct, _ago(M1),    Decimal("3_500.00"),  "RetailCo Inc Salary",  retailco.id,    cats["Salary"].id),
        _txn(acct, _ago(M1-2),  Decimal("-1_600.00"), "Monthly Rent Payment", None,           cats["Rent"].id),
        _txn(acct, _ago(M1-4),  Decimal("-412.80"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(acct, _ago(M1-5),  Decimal("-8.50"),     "Starbucks",            starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M1-6),  Decimal("-8.20"),     "Starbucks Morning",    starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M1-7),  Decimal("-78.50"),    "Uber Trip",            uber.id,        cats["Transport"].id),
        _txn(acct, _ago(M1-8),  Decimal("-280.00"),   "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
        _txn(acct, _ago(M1-10), Decimal("-195.30"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(acct, _ago(M1-11), Decimal("-15.99"),    "Netflix Subscription", netflix.id,     cats["Entertainment"].id),
        _txn(acct, _ago(M1-13), Decimal("-7.50"),     "Starbucks Coffee",     starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M1-14), Decimal("-195.00"),   "Target Store",         target.id,      cats["Shopping"].id),
        _txn(acct, _ago(M1-16), Decimal("-65.00"),    "DoorDash Order",       doordash.id,    cats["Dining Out"].id),
        _txn(acct, _ago(M1-18), Decimal("-52.00"),    "DoorDash Dinner",      doordash.id,    cats["Dining Out"].id),
        _txn(acct, _ago(M1-20), Decimal("-145.00"),   "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
        _txn(acct, _ago(M1-22), Decimal("-48.00"),    "DoorDash Lunch",       doordash.id,    cats["Dining Out"].id),
    ]

    M2 = 35
    txns += [
        _txn(acct, _ago(M2),    Decimal("3_500.00"),  "RetailCo Inc Salary",  retailco.id,    cats["Salary"].id),
        _txn(acct, _ago(M2-2),  Decimal("-1_600.00"), "Monthly Rent Payment", None,           cats["Rent"].id),
        _txn(acct, _ago(M2-4),  Decimal("-389.50"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(acct, _ago(M2-5),  Decimal("-7.80"),     "Starbucks",            starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M2-6),  Decimal("-8.00"),     "Starbucks Morning",    starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M2-7),  Decimal("-65.00"),    "Uber Trip",            uber.id,        cats["Transport"].id),
        _txn(acct, _ago(M2-9),  Decimal("-320.00"),   "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
        _txn(acct, _ago(M2-10), Decimal("-210.00"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(acct, _ago(M2-12), Decimal("-15.99"),    "Netflix Subscription", netflix.id,     cats["Entertainment"].id),
        _txn(acct, _ago(M2-14), Decimal("-7.50"),     "Starbucks Coffee",     starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(M2-16), Decimal("-175.00"),   "Target Store",         target.id,      cats["Shopping"].id),
        _txn(acct, _ago(M2-18), Decimal("-71.00"),    "DoorDash Order",       doordash.id,    cats["Dining Out"].id),
        _txn(acct, _ago(M2-20), Decimal("-58.50"),    "DoorDash Dinner",      doordash.id,    cats["Dining Out"].id),
        _txn(acct, _ago(M2-22), Decimal("-160.00"),   "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
        _txn(acct, _ago(M2-24), Decimal("-42.00"),    "DoorDash Lunch",       doordash.id,    cats["Dining Out"].id),
    ]

    txns += [
        _txn(acct, _ago(14), Decimal("3_500.00"),  "RetailCo Inc Salary",  retailco.id,    cats["Salary"].id),
        _txn(acct, _ago(12), Decimal("-1_600.00"), "Monthly Rent Payment", None,           cats["Rent"].id),
        _txn(acct, _ago(10), Decimal("-380.00"),   "Whole Foods Market",   whole_foods.id, cats["Groceries"].id),
        _txn(acct, _ago(8),  Decimal("-8.50"),     "Starbucks",            starbucks.id,   cats["Coffee"].id),
        _txn(acct, _ago(7),  Decimal("-280.00"),   "Amazon Purchase",      amazon.id,      cats["Shopping"].id),
        _txn(acct, _ago(5),  Decimal("-55.00"),    "DoorDash Order",       doordash.id,    cats["Dining Out"].id),
        _txn(acct, _ago(3),  Decimal("-48.00"),    "Uber Trip",            uber.id,        cats["Transport"].id),
    ]

    db.add_all(txns)
    await db.flush()

    # ── Budget (tight — clearly exceeded) ─────────────────────────────────
    month_start, month_end = _current_month_range()
    budget = Budget(
        user_id=user.id,
        name="Tight Monthly Budget",
        period_start=month_start,
        period_end=month_end,
        period_type="monthly",
        thresholds=[0.8, 0.9, 1.0],
    )
    db.add(budget)
    await db.flush()
    db.add_all([
        BudgetItem(budget_id=budget.id, category_id=cats["Groceries"].id,     limit_amount=Decimal("300.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Shopping"].id,      limit_amount=Decimal("150.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Entertainment"].id, limit_amount=Decimal("30.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Dining Out"].id,    limit_amount=Decimal("60.00")),
        BudgetItem(budget_id=budget.id, category_id=cats["Transport"].id,     limit_amount=Decimal("50.00")),
    ])
    await db.flush()

    # ── Alerts — 2 severe unread + 1 minor read ───────────────────────────
    db.add_all([
        BudgetAlert(
            user_id=user.id, budget_id=budget.id,
            category_id=cats["Groceries"].id,
            threshold_percent=Decimal("1.000"),
            spent_amount=Decimal("380.00"), limit_amount=Decimal("300.00"),
            period_start=month_start, period_end=month_end,
            is_read=False,
        ),
        BudgetAlert(
            user_id=user.id, budget_id=budget.id,
            category_id=cats["Shopping"].id,
            threshold_percent=Decimal("1.000"),
            spent_amount=Decimal("280.00"), limit_amount=Decimal("150.00"),
            period_start=month_start, period_end=month_end,
            is_read=False,
        ),
        BudgetAlert(
            user_id=user.id, budget_id=budget.id,
            category_id=cats["Dining Out"].id,
            threshold_percent=Decimal("0.900"),
            spent_amount=Decimal("55.00"), limit_amount=Decimal("60.00"),
            period_start=month_start, period_end=month_end,
            is_read=True,
        ),
    ])
    await db.flush()

    # ── Recommendation run (all gates blocked) ────────────────────────────
    s_answers = {
        "market_drop_reaction": 2,
        "investment_experience": 1,
        "income_stability": 2,
        "loss_tolerance_pct": 1,
        "goal_priority": 2,
    }
    outputs_s, action_items_s = _rec_outputs(
        monthly_income=Decimal("3_500.00"),
        monthly_spending=Decimal("2_960.00"),   # expenses only
        balance=Decimal("850.00"),              # tiny emergency fund → FAIL gate
        answers=s_answers,
        horizon=36,
        liquidity="high",
        severe_alerts=2,                        # 2 severe alerts → FAIL gate
        run_seed=2002,
    )
    db.add(RiskProfile(
        user_id=user.id,
        score=compute_risk_score(s_answers),
        horizon_months=36,
        liquidity_need="high",
        answers_json=s_answers,
    ))
    rec_run_s = RecommendationRun(
        user_id=user.id,
        status="completed",
        inputs_snapshot={
            "score": outputs_s["risk_score"],
            "horizon_months": 36,
            "liquidity_need": "high",
            "needs_profile": False,
        },
        outputs=outputs_s,
    )
    db.add(rec_run_s)
    await db.flush()
    for item_data in action_items_s:
        db.add(RecommendationItem(
            run_id=rec_run_s.id,
            priority=item_data["priority"],
            type=item_data["type"],
            title=item_data["title"],
            details=item_data.get("details"),
            confidence=Decimal(str(item_data["confidence"])),
        ))
    await db.flush()

    # ── Advisor chat ──────────────────────────────────────────────────────
    ef_str = f"{outputs_s['emergency_fund_months']:.1f}"
    session_s = ChatSession(user_id=user.id, title="Help with overspending")
    db.add(session_s)
    await db.flush()
    db.add_all([
        ChatMessage(session_id=session_s.id, role="user", created_at=_at(2, 9),
            content="I keep going over budget every month. What should I do?"),
        ChatMessage(session_id=session_s.id, role="assistant", created_at=_at(2, 9),
            content=(
                "I can see you're under real financial pressure. "
                f"Your emergency fund covers only about {ef_str} months of expenses — "
                "well below the 3-month minimum — and 2 budget categories are over 100% "
                "spent this month (Groceries and Shopping). "
                "Before investing: (1) freeze non-essential spending, "
                "(2) redirect all surplus to your emergency fund, "
                "(3) cap Groceries at $250 and pause online shopping for 30 days. "
                "Would you like a concrete weekly spending plan?"
            )),
        ChatMessage(session_id=session_s.id, role="user", created_at=_at(2, 9),
            content="Yes, please help me create a plan."),
        ChatMessage(session_id=session_s.id, role="assistant", created_at=_at(2, 9),
            content=(
                "Here's a focused plan: "
                "• Groceries: cap at $250 — meal-plan 5 meals/week, shop once. "
                "• Shopping: pause all online orders for 30 days. "
                "• DoorDash: limit to once/week (saves ~$150/month). "
                "• Transport: combine Uber trips or use public transit. "
                "Together these changes can free up $300–400/month. Direct every dollar to "
                "your emergency fund until you reach 1 month of expenses covered."
            )),
    ])
    await db.flush()

    return {
        "accounts": 1,
        "transactions": len(txns),
        "budgets": True,
        "alerts": True,
        "recommendations": True,
        "reports": False,
        "import_session": False,
        "chat": True,
    }


# ---------------------------------------------------------------------------
# C) newuser@example.com
# ---------------------------------------------------------------------------

async def seed_newuser(db: AsyncSession) -> dict:
    user = User(
        email="newuser@example.com",
        name="Sam Taylor",
        hashed_password=get_password_hash(DEMO_PASSWORD),
    )
    db.add(user)
    await db.flush()

    bofa = await _upsert_institution(db, "Bank of America")
    checking = BankAccount(
        user_id=user.id, institution_id=bofa.id,
        name="BofA Checking", currency="USD",
        balance=Decimal("2_300.00"),
        bank_account_number_last4="7712",
    )
    db.add(checking)
    await db.flush()

    cats: dict[str, Category] = {}
    for name, ctype, rules in [
        ("Salary",    "income",  []),
        ("Food",      "expense", [
            {"pattern": "whole foods", "match": "contains", "priority": 10},
            {"pattern": "starbucks",   "match": "contains", "priority": 10},
        ]),
        ("Transport", "expense", [{"pattern": "uber", "match": "contains", "priority": 10}]),
        ("Other",     "expense", []),
    ]:
        c = Category(user_id=user.id, name=name, type=ctype, rules=rules)
        db.add(c)
        cats[name] = c
    await db.flush()

    uber        = await _upsert_merchant(db, "Uber")
    whole_foods = await _upsert_merchant(db, "Whole Foods")
    starbucks   = await _upsert_merchant(db, "Starbucks")
    amazon      = await _upsert_merchant(db, "Amazon")

    acct = checking.id
    txns = [
        _txn(acct, _ago(14), Decimal("2_500.00"),  "Direct Deposit Salary", None,           cats["Salary"].id),
        _txn(acct, _ago(12), Decimal("-850.00"),   "Monthly Rent",          None,           cats["Other"].id),
        _txn(acct, _ago(10), Decimal("-95.20"),    "Whole Foods Market",    whole_foods.id, cats["Food"].id),
        _txn(acct, _ago(9),  Decimal("-6.50"),     "Starbucks",             starbucks.id,   cats["Food"].id),
        _txn(acct, _ago(8),  Decimal("-22.00"),    "Uber Trip",             uber.id,        cats["Transport"].id),
        _txn(acct, _ago(6),  Decimal("-78.00"),    "Amazon Purchase",       amazon.id,      cats["Other"].id),
        _txn(acct, _ago(5),  Decimal("-62.40"),    "Whole Foods Market",    whole_foods.id, cats["Food"].id),
        _txn(acct, _ago(4),  Decimal("-5.80"),     "Starbucks Morning",     starbucks.id,   cats["Food"].id),
        _txn(acct, _ago(3),  Decimal("-18.50"),    "Uber Trip",             uber.id,        cats["Transport"].id),
        _txn(acct, _ago(1),  Decimal("-45.00"),    "Online Purchase",       amazon.id,      cats["Other"].id),
    ]
    db.add_all(txns)
    await db.flush()

    month_start, month_end = _current_month_range()
    budget = Budget(
        user_id=user.id,
        name="My First Budget",
        period_start=month_start,
        period_end=month_end,
        period_type="monthly",
        thresholds=[0.8, 0.9, 1.0],
    )
    db.add(budget)
    await db.flush()
    db.add(BudgetItem(
        budget_id=budget.id,
        category_id=cats["Food"].id,
        limit_amount=Decimal("200.00"),
    ))
    await db.flush()

    return {
        "accounts": 1,
        "transactions": len(txns),
        "budgets": True,
        "alerts": False,
        "recommendations": False,
        "reports": False,
        "import_session": False,
        "chat": False,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    demo_emails = [
        "healthy@example.com",
        "stressed@example.com",
        "newuser@example.com",
    ]

    print("━" * 60)
    print("BudgetFlowApp Demo Seeder")
    print("━" * 60)
    print("Step 1/2 — removing existing demo users (cascade)...")
    async with AsyncSessionLocal() as db:
        for email in demo_emails:
            await _delete_demo_user(db, email)
        await db.commit()

    print("Step 2/2 — seeding fresh demo data...")

    results: list[tuple[str, str, dict]] = []

    async with AsyncSessionLocal() as db:
        stats = await seed_healthy(db)
        await db.commit()
    results.append(("Alex Morgan", "healthy@example.com", stats))

    async with AsyncSessionLocal() as db:
        stats = await seed_stressed(db)
        await db.commit()
    results.append(("Jordan Lee", "stressed@example.com", stats))

    async with AsyncSessionLocal() as db:
        stats = await seed_newuser(db)
        await db.commit()
    results.append(("Sam Taylor", "newuser@example.com", stats))

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("━" * 60)
    print("SEED COMPLETE — Demo Accounts")
    print("━" * 60)
    for name, email, s in results:
        tick = lambda v: "✓" if v else "–"
        print(f"\n  {name}  <{email}>")
        print(f"  Password:        {DEMO_PASSWORD}")
        print(f"  Accounts:        {s['accounts']}")
        print(f"  Transactions:    {s['transactions']}", end="")
        if "transactions_on_checking" in s:
            print(f"  (BofA: {s['transactions_on_checking']} | Amex: {s['transactions_on_amex']})", end="")
        print()
        print(f"  Budgets:         {tick(s['budgets'])}")
        print(f"  Alerts:          {tick(s['alerts'])}")
        print(f"  Recommendations: {tick(s['recommendations'])}")
        print(f"  Reports:         {tick(s['reports'])}")
        print(f"  Import session:  {tick(s['import_session'])}")
        print(f"  Advisor Chat:    {tick(s['chat'])}")

    print()
    print("━" * 60)
    print("Frontend: http://localhost:3000   API: http://localhost:8000")
    print("━" * 60)


if __name__ == "__main__":
    asyncio.run(main())
