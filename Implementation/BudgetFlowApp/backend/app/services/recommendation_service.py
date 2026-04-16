"""
UC08 Investment Recommendation Engine.

Deterministic, explainable, no LLM dependency.
All financial computations use user-scoped data from existing services.

Monte Carlo assumptions (documented):
- Conservative annual return: based on risk bucket (3-9% nominal).
- Annual volatility: based on risk bucket (4-18%).
- Inflation: 2.5% assumed.
- Simulations: 500 paths, seeded per run_id for reproducibility.
"""
import hashlib
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Literal

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import FinancialAccount
from app.models.alert import BudgetAlert
from app.models.budget import Budget, BudgetItem
from app.models.recommendation import RiskProfile, RecommendationRun, RecommendationItem
from app.models.transaction import Transaction


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RISK_BUCKETS = {
    "conservative":  {"label": "Conservative",  "min": 0,  "max": 30,  "return_pct": 0.04, "vol_pct": 0.05},
    "moderate_conservative": {"label": "Moderate Conservative", "min": 31, "max": 45, "return_pct": 0.055, "vol_pct": 0.08},
    "balanced":      {"label": "Balanced",       "min": 46, "max": 60,  "return_pct": 0.07, "vol_pct": 0.12},
    "moderate_growth": {"label": "Moderate Growth", "min": 61, "max": 75, "return_pct": 0.08, "vol_pct": 0.15},
    "growth":        {"label": "Growth",         "min": 76, "max": 100, "return_pct": 0.09, "vol_pct": 0.18},
}

MODEL_PORTFOLIOS = {
    "conservative": [
        {"asset": "US Short-Term Bonds", "ticker": "SHV",  "pct": 40, "rationale": "Capital preservation and liquidity"},
        {"asset": "US Aggregate Bonds",  "ticker": "AGG",  "pct": 35, "rationale": "Stable income with low volatility"},
        {"asset": "US Large Cap",        "ticker": "VTI",  "pct": 15, "rationale": "Modest equity growth exposure"},
        {"asset": "International Bonds", "ticker": "BNDX", "pct": 10, "rationale": "Global diversification in fixed income"},
    ],
    "moderate_conservative": [
        {"asset": "US Aggregate Bonds",  "ticker": "AGG",  "pct": 35, "rationale": "Core fixed-income stability"},
        {"asset": "US Large Cap",        "ticker": "VTI",  "pct": 30, "rationale": "Broad US equity exposure"},
        {"asset": "International Equity","ticker": "VXUS", "pct": 15, "rationale": "Geographic diversification"},
        {"asset": "US Short-Term Bonds", "ticker": "SHV",  "pct": 10, "rationale": "Liquidity buffer"},
        {"asset": "TIPS",                "ticker": "TIP",  "pct": 10, "rationale": "Inflation protection"},
    ],
    "balanced": [
        {"asset": "US Large Cap",        "ticker": "VTI",  "pct": 35, "rationale": "Core US equity growth"},
        {"asset": "International Equity","ticker": "VXUS", "pct": 20, "rationale": "Global equity diversification"},
        {"asset": "US Aggregate Bonds",  "ticker": "AGG",  "pct": 25, "rationale": "Portfolio stabilizer"},
        {"asset": "Real Estate",         "ticker": "VNQ",  "pct": 10, "rationale": "Real asset diversification and income"},
        {"asset": "TIPS",                "ticker": "TIP",  "pct": 10, "rationale": "Inflation hedge"},
    ],
    "moderate_growth": [
        {"asset": "US Large Cap",        "ticker": "VTI",  "pct": 40, "rationale": "Primary growth driver"},
        {"asset": "International Equity","ticker": "VXUS", "pct": 25, "rationale": "Global growth exposure"},
        {"asset": "US Aggregate Bonds",  "ticker": "AGG",  "pct": 15, "rationale": "Volatility reduction"},
        {"asset": "Small Cap",           "ticker": "VB",   "pct": 10, "rationale": "Higher growth potential"},
        {"asset": "Real Estate",         "ticker": "VNQ",  "pct": 10, "rationale": "Alternative asset class"},
    ],
    "growth": [
        {"asset": "US Large Cap",        "ticker": "VTI",  "pct": 40, "rationale": "Core equity position"},
        {"asset": "International Equity","ticker": "VXUS", "pct": 25, "rationale": "Broad international exposure"},
        {"asset": "Small Cap",           "ticker": "VB",   "pct": 15, "rationale": "Enhanced growth potential"},
        {"asset": "Emerging Markets",    "ticker": "VWO",  "pct": 10, "rationale": "High-growth markets exposure"},
        {"asset": "US Aggregate Bonds",  "ticker": "AGG",  "pct": 10, "rationale": "Minimal stability allocation"},
    ],
}

INFLATION_RATE = 0.025
BUFFER_FACTOR = 0.80
EMERGENCY_TARGET_MONTHS = 3.0
SIM_PATHS = 500
BUCKET_ORDER = ["conservative", "moderate_conservative", "balanced", "moderate_growth", "growth"]
GoalType = Literal["retirement", "house", "emergency", "general"]


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_risk_score(answers: dict) -> int:
    """Convert 5 answers (each 1-5) into a 0-100 score."""
    keys = ["market_drop_reaction", "investment_experience",
            "income_stability", "loss_tolerance_pct", "goal_priority"]
    total = sum(answers.get(k, 3) for k in keys)
    return int(round((total - 5) / 20 * 100))


def risk_bucket_for_score(score: int, horizon_months: int) -> str:
    adjusted = score
    if horizon_months < 24:
        adjusted = max(0, adjusted - 15)
    elif horizon_months < 36:
        adjusted = max(0, adjusted - 5)
    elif horizon_months > 120:
        adjusted = min(100, adjusted + 5)

    for key, cfg in RISK_BUCKETS.items():
        if cfg["min"] <= adjusted <= cfg["max"]:
            return key
    return "balanced"


def _shift_bucket(bucket: str, steps: int) -> str:
    idx = BUCKET_ORDER.index(bucket) if bucket in BUCKET_ORDER else BUCKET_ORDER.index("balanced")
    shifted = max(0, min(len(BUCKET_ORDER) - 1, idx + steps))
    return BUCKET_ORDER[shifted]


def apply_goal_mode(
    base_bucket: str,
    goal_type: Optional[GoalType],
    horizon_months: int,
) -> tuple[str, str]:
    """
    Deterministically adjust bucket based on explicit goal and horizon.
    This keeps core risk logic unchanged while making recommendations more contextual.
    """
    if not goal_type:
        return base_bucket, "No goal override applied; using your baseline risk profile."

    steps = 0
    reason_parts: list[str] = []

    if goal_type == "emergency":
        steps -= 2
        reason_parts.append("emergency goal prioritizes capital preservation")
    elif goal_type == "house":
        if horizon_months <= 60:
            steps -= 1
            reason_parts.append("house goal with medium/short horizon favors lower volatility")
    elif goal_type == "retirement":
        if horizon_months >= 120:
            steps += 1
            reason_parts.append("retirement goal with long horizon supports more growth exposure")
    elif goal_type == "general":
        reason_parts.append("general goal keeps baseline risk posture")

    if horizon_months <= 24:
        steps -= 1
        reason_parts.append("short horizon reduces risk tolerance")
    elif horizon_months >= 180 and goal_type in {"retirement", "general"}:
        steps += 1
        reason_parts.append("very long horizon can absorb more volatility")

    adjusted = _shift_bucket(base_bucket, steps)
    if adjusted == base_bucket:
        return adjusted, "; ".join(reason_parts) or "Goal profile aligns with baseline bucket."
    return adjusted, "; ".join(reason_parts) or "Goal and horizon adjusted the risk bucket."


# ---------------------------------------------------------------------------
# Data-fetching helpers (user-scoped)
# ---------------------------------------------------------------------------

async def _monthly_spending_avg(db: AsyncSession, user_id: uuid.UUID, months: int = 3) -> Decimal:
    """Average monthly absolute spending over last N months (negative-amount txns only)."""
    cutoff = date.today() - timedelta(days=months * 30)
    stmt = (
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(
            FinancialAccount.user_id == user_id,
            Transaction.posted_date >= cutoff,
            Transaction.amount < 0,
        )
    )
    total = (await db.execute(stmt)).scalar() or Decimal("0")
    return total / max(months, 1)


async def _monthly_income_estimate(db: AsyncSession, user_id: uuid.UUID, months: int = 3) -> Decimal:
    """Estimate monthly income as sum of positive-amount transactions."""
    cutoff = date.today() - timedelta(days=months * 30)
    stmt = (
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(
            FinancialAccount.user_id == user_id,
            Transaction.posted_date >= cutoff,
            Transaction.amount > 0,
        )
    )
    total = (await db.execute(stmt)).scalar() or Decimal("0")
    return total / max(months, 1)


async def _total_balance(db: AsyncSession, user_id: uuid.UUID) -> Decimal:
    stmt = (
        select(func.coalesce(func.sum(FinancialAccount.balance), 0))
        .where(FinancialAccount.user_id == user_id, FinancialAccount.is_active.is_(True))
    )
    return (await db.execute(stmt)).scalar() or Decimal("0")


async def _severe_alert_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Count unread alerts where spending >= 100% of budget limit."""
    stmt = (
        select(func.count(BudgetAlert.id))
        .where(
            BudgetAlert.user_id == user_id,
            BudgetAlert.is_read.is_(False),
            BudgetAlert.threshold_percent >= Decimal("1.0"),
        )
    )
    return (await db.execute(stmt)).scalar() or 0


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_emergency_fund_months(balance: Decimal, monthly_spending: Decimal) -> float:
    if monthly_spending <= 0:
        return 99.0
    return round(float(balance) / float(monthly_spending), 2)


def compute_investable_amount(
    monthly_income: Decimal, monthly_spending: Decimal,
) -> float:
    surplus = float(monthly_income) - float(monthly_spending)
    if surplus <= 0:
        return 0.0
    return round(surplus * BUFFER_FACTOR, 2)


def compute_contribution_tiers(
    monthly_income: Decimal,
    monthly_spending: Decimal,
    investable: float,
) -> Optional[dict]:
    """
    Build conservative contribution tiers from real cashflow.
    - safe: half of raw surplus (defensive pace)
    - recommended: buffered investable amount
    - stretch: up to 95% of surplus, capped to avoid reckless over-allocation
    """
    if investable <= 0:
        return None

    surplus = max(float(monthly_income) - float(monthly_spending), 0.0)
    if surplus <= 0:
        return None

    safe = min(investable, surplus * 0.50)
    recommended = investable
    stretch = min(surplus * 0.95, recommended * 1.25)
    stretch = max(stretch, recommended)
    safe = min(safe, recommended)

    return {
        "safe_contribution_monthly": round(safe, 2),
        "recommended_contribution_monthly": round(recommended, 2),
        "stretch_contribution_monthly": round(stretch, 2),
    }


def rules_gates(
    emergency_months: float,
    cashflow_positive: bool,
    severe_alerts: int,
) -> list[str]:
    """Return list of safety warnings. Non-empty means investing is restricted."""
    warnings: list[str] = []
    if emergency_months < 1.0:
        warnings.append(
            f"Emergency fund covers only {emergency_months:.1f} months of expenses. "
            f"Build at least {EMERGENCY_TARGET_MONTHS:.0f} months before investing."
        )
    if not cashflow_positive:
        warnings.append(
            "Your spending exceeds your income. Focus on reducing expenses or increasing income first."
        )
    if severe_alerts >= 2:
        warnings.append(
            f"You have {severe_alerts} budget categories over 100% spent. "
            "Stabilize your budget before allocating to investments."
        )
    return warnings


def rules_gates_structured(
    emergency_months: float,
    cashflow_positive: bool,
    severe_alerts: int,
) -> list[dict]:
    """Structured gate results for explainability."""
    gates = [
        {
            "code": "EMERGENCY_FUND",
            "passed": emergency_months >= 1.0,
            "reason": (
                f"Emergency fund covers {emergency_months:.1f} months"
                if emergency_months >= 1.0
                else f"Emergency fund only {emergency_months:.1f} months (need >= 1)"
            ),
        },
        {
            "code": "POSITIVE_CASHFLOW",
            "passed": cashflow_positive,
            "reason": (
                "Income exceeds expenses"
                if cashflow_positive
                else "Expenses exceed income"
            ),
        },
        {
            "code": "BUDGET_HEALTH",
            "passed": severe_alerts < 2,
            "reason": (
                f"{severe_alerts} severe alert(s) (< 2 threshold)"
                if severe_alerts < 2
                else f"{severe_alerts} budget categories over 100% spent"
            ),
        },
    ]
    return gates


def derive_unlock_actions(
    emergency_months: float,
    cashflow_positive: bool,
    severe_alerts: int,
) -> list[str]:
    actions: list[str] = []
    if emergency_months < 1.0:
        actions.append("Build emergency fund coverage to at least 1 month of expenses.")
    if not cashflow_positive:
        actions.append("Restore positive cashflow by cutting discretionary spend or increasing income.")
    if severe_alerts >= 2:
        actions.append("Reduce overspent budget categories below 100% utilization.")
    return actions


def explain_bucket_choice(
    score: int,
    base_bucket: str,
    final_bucket: str,
    goal_reason: str,
    goal_type: Optional[GoalType],
) -> str:
    base_label = RISK_BUCKETS[base_bucket]["label"]
    final_label = RISK_BUCKETS[final_bucket]["label"]
    if final_bucket == base_bucket:
        return (
            f"Risk score {score} maps to {base_label}. "
            f"{goal_reason}"
        )
    goal_txt = goal_type or "goal preferences"
    return (
        f"Risk score {score} maps to {base_label}, then adjusted to {final_label} "
        f"for {goal_txt}. {goal_reason}"
    )


def explain_now_or_not_now(warnings: list[str]) -> str:
    if warnings:
        return "Investing is blocked for now because one or more safety gates failed."
    return "Investing is allowed now because safety gates passed and surplus is available."


def downside_note_for_bucket(bucket: str) -> str:
    vol = RISK_BUCKETS[bucket]["vol_pct"] * 100
    return (
        f"{RISK_BUCKETS[bucket]['label']} portfolios can still decline in volatile markets "
        f"(model volatility ~{vol:.1f}% annually). Projections are not guarantees."
    )


def _validate_allocation_invariant(allocation: list[dict]) -> None:
    """Raise if allocation weights don't sum to ~100%."""
    if not allocation:
        return
    total = sum(a["pct"] for a in allocation)
    if abs(total - 100.0) > 0.1:
        raise RuntimeError(
            f"INVARIANT VIOLATION: allocation weights sum to {total}, expected 100.0"
        )


def _validate_projection_invariant(projection: list[dict]) -> None:
    """Raise if p10 > median or median > p90 for any point."""
    for pt in projection:
        if pt["p10"] > pt["median"] + 0.01 or pt["median"] > pt["p90"] + 0.01:
            raise RuntimeError(
                f"INVARIANT VIOLATION at month {pt['month']}: "
                f"p10={pt['p10']}, median={pt['median']}, p90={pt['p90']}"
            )


def model_portfolio(bucket: str) -> list[dict]:
    return MODEL_PORTFOLIOS.get(bucket, MODEL_PORTFOLIOS["balanced"])


def run_projection(
    monthly_contribution: float,
    initial_balance: float,
    horizon_months: int,
    annual_return: float,
    annual_vol: float,
    run_seed: int,
) -> list[dict]:
    """
    Monte Carlo projection.
    Returns monthly snapshots with p10, median, p90.
    Uses geometric Brownian motion with monthly steps.

    Assumptions:
    - Returns are log-normal distributed.
    - Monthly return = annual_return/12, monthly vol = annual_vol/sqrt(12).
    - Contributions added at start of each month.
    - All values nominal (not inflation-adjusted).
    """
    rng = np.random.default_rng(seed=run_seed)
    monthly_ret = annual_return / 12
    monthly_vol = annual_vol / math.sqrt(12)

    sample_months = list(range(0, horizon_months + 1, max(1, horizon_months // 24)))
    if horizon_months not in sample_months:
        sample_months.append(horizon_months)
    sample_months = sorted(set(sample_months))

    paths = np.zeros((SIM_PATHS, horizon_months + 1))
    paths[:, 0] = initial_balance

    shocks = rng.normal(monthly_ret, monthly_vol, (SIM_PATHS, horizon_months))

    for m in range(1, horizon_months + 1):
        paths[:, m] = (paths[:, m - 1] + monthly_contribution) * (1 + shocks[:, m - 1])
        np.clip(paths[:, m], 0, None, out=paths[:, m])

    result = []
    for m in sample_months:
        col = paths[:, m]
        result.append({
            "month": m,
            "median": round(float(np.median(col)), 2),
            "p10": round(float(np.percentile(col, 10)), 2),
            "p90": round(float(np.percentile(col, 90)), 2),
        })
    return result


# ---------------------------------------------------------------------------
# Action-item assembly
# ---------------------------------------------------------------------------

def _build_action_items(
    warnings: list[str],
    emergency_months: float,
    cashflow_positive: bool,
    investable: float,
    bucket: str,
    allocation: list[dict],
) -> list[dict]:
    items: list[dict] = []
    priority = 1

    if emergency_months < EMERGENCY_TARGET_MONTHS:
        gap = EMERGENCY_TARGET_MONTHS - emergency_months
        items.append({
            "priority": priority, "type": "emergency_fund",
            "title": f"Build emergency fund ({gap:.1f} more months needed)",
            "details": {
                "current_months": emergency_months,
                "target_months": EMERGENCY_TARGET_MONTHS,
                "explanation": "An emergency fund covering 3 months of expenses protects you from unexpected job loss or medical bills.",
            },
            "confidence": 0.95,
        })
        priority += 1

    if not cashflow_positive:
        items.append({
            "priority": priority, "type": "reduce_spending",
            "title": "Reduce spending to achieve positive cash flow",
            "details": {
                "explanation": "You are spending more than you earn. Review your largest expense categories and identify cuts.",
            },
            "confidence": 0.95,
        })
        priority += 1

    if warnings:
        items.append({
            "priority": priority, "type": "stabilize",
            "title": "Stabilize finances before investing",
            "details": {
                "warnings": warnings,
                "explanation": "Address the issues above before directing money to investments.",
            },
            "confidence": 0.90,
        })
        priority += 1

    if not warnings and investable > 0:
        items.append({
            "priority": priority, "type": "invest",
            "title": f"Invest ${investable:,.0f}/month in {RISK_BUCKETS[bucket]['label']} portfolio",
            "details": {
                "monthly_amount": investable,
                "risk_bucket": bucket,
                "allocation_summary": [f"{a['ticker']} ({a['pct']}%)" for a in allocation],
                "explanation": f"Based on your risk profile and surplus, invest in a diversified {RISK_BUCKETS[bucket]['label'].lower()} ETF portfolio.",
            },
            "confidence": 0.80,
        })
        priority += 1

        if emergency_months < 6.0:
            items.append({
                "priority": priority, "type": "continue_saving",
                "title": "Continue growing emergency fund to 6 months",
                "details": {
                    "current_months": emergency_months,
                    "target_months": 6.0,
                    "explanation": "While investing, keep building your emergency fund toward the recommended 6-month cushion.",
                },
                "confidence": 0.75,
            })
            priority += 1

    if not warnings and investable <= 0 and cashflow_positive:
        items.append({
            "priority": priority, "type": "increase_income",
            "title": "Look for ways to increase your investable surplus",
            "details": {
                "explanation": "Your finances are stable but there is little surplus after the safety buffer. Consider reducing discretionary spending or increasing income.",
            },
            "confidence": 0.65,
        })
        priority += 1

    return items


# ---------------------------------------------------------------------------
# Upsert risk profile
# ---------------------------------------------------------------------------

async def upsert_risk_profile(
    db: AsyncSession, user_id: uuid.UUID,
    answers: dict, horizon_months: int, liquidity_need: str,
) -> RiskProfile:
    score = compute_risk_score(answers)
    result = await db.execute(
        select(RiskProfile).where(RiskProfile.user_id == user_id)
    )
    profile = result.scalars().first()
    if profile:
        profile.score = score
        profile.horizon_months = horizon_months
        profile.liquidity_need = liquidity_need
        profile.answers_json = answers
        profile.updated_at = datetime.now(timezone.utc)
    else:
        profile = RiskProfile(
            user_id=user_id, score=score,
            horizon_months=horizon_months,
            liquidity_need=liquidity_need,
            answers_json=answers,
        )
        db.add(profile)
    await db.flush()
    return profile


async def get_risk_profile(db: AsyncSession, user_id: uuid.UUID) -> Optional[RiskProfile]:
    result = await db.execute(
        select(RiskProfile).where(RiskProfile.user_id == user_id)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Run orchestrator
# ---------------------------------------------------------------------------

def _seed_from_uuid(run_id: uuid.UUID) -> int:
    return int(hashlib.sha256(run_id.bytes).hexdigest()[:8], 16)


async def execute_run(
    db: AsyncSession,
    user_id: uuid.UUID,
    risk_profile_input: Optional[dict] = None,
    horizon_override: Optional[int] = None,
    goal_type: Optional[GoalType] = None,
    target_horizon_months: Optional[int] = None,
    override_contribution_monthly: Optional[float] = None,
) -> RecommendationRun:
    if risk_profile_input:
        answers = risk_profile_input["answers"]
        horizon = risk_profile_input.get("horizon_months", 60)
        liquidity = risk_profile_input.get("liquidity_need", "moderate")
        profile = await upsert_risk_profile(db, user_id, answers, horizon, liquidity)
    else:
        profile = await get_risk_profile(db, user_id)

    needs_profile = profile is None
    score = profile.score if profile else 50
    horizon = target_horizon_months or horizon_override or (profile.horizon_months if profile else 60)
    liquidity = profile.liquidity_need if profile else "moderate"

    monthly_spending = await _monthly_spending_avg(db, user_id, months=3)
    monthly_income = await _monthly_income_estimate(db, user_id, months=3)
    balance = await _total_balance(db, user_id)
    severe_alerts = await _severe_alert_count(db, user_id)

    emergency_months = compute_emergency_fund_months(balance, monthly_spending)
    cashflow_positive = float(monthly_income) >= float(monthly_spending)
    investable = compute_investable_amount(monthly_income, monthly_spending)

    base_bucket = risk_bucket_for_score(score, horizon)
    bucket, goal_reason = apply_goal_mode(base_bucket, goal_type, horizon)
    warnings = rules_gates(emergency_months, cashflow_positive, severe_alerts)
    gates = rules_gates_structured(emergency_months, cashflow_positive, severe_alerts)
    unlock_actions = derive_unlock_actions(emergency_months, cashflow_positive, severe_alerts)

    horizon_adj = 0
    if horizon < 24:
        horizon_adj = -15
    elif horizon < 36:
        horizon_adj = -5
    elif horizon > 120:
        horizon_adj = 5

    tiers = compute_contribution_tiers(monthly_income, monthly_spending, investable) if not warnings else None
    recommended_contribution = tiers["recommended_contribution_monthly"] if tiers else 0.0
    effective_contribution = recommended_contribution
    if override_contribution_monthly is not None and not warnings:
        effective_contribution = round(float(override_contribution_monthly), 2)
    if warnings:
        investable = 0.0
        effective_contribution = 0.0

    allocation = model_portfolio(bucket) if not warnings else []
    _validate_allocation_invariant(allocation)

    action_items = _build_action_items(
        warnings, emergency_months, cashflow_positive, effective_contribution, bucket, allocation,
    )

    run = RecommendationRun(user_id=user_id, status="completed")
    db.add(run)
    await db.flush()

    run_seed = _seed_from_uuid(run.id)
    bucket_cfg = RISK_BUCKETS[bucket]

    projection: list[dict] = []
    assumptions: dict = {
        "expected_return": bucket_cfg["return_pct"],
        "volatility": bucket_cfg["vol_pct"],
        "paths": SIM_PATHS,
        "step": "monthly",
        "inflation_assumed": INFLATION_RATE,
        "buffer_factor": BUFFER_FACTOR,
    }
    baseline_projection: list[dict] = []
    if not warnings and recommended_contribution > 0:
        baseline_projection = run_projection(
            monthly_contribution=recommended_contribution,
            initial_balance=float(balance),
            horizon_months=horizon,
            annual_return=bucket_cfg["return_pct"],
            annual_vol=bucket_cfg["vol_pct"],
            run_seed=run_seed,
        )
        _validate_projection_invariant(baseline_projection)
    if not warnings and effective_contribution > 0:
        projection = run_projection(
            monthly_contribution=effective_contribution,
            initial_balance=float(balance),
            horizon_months=horizon,
            annual_return=bucket_cfg["return_pct"],
            annual_vol=bucket_cfg["vol_pct"],
            run_seed=run_seed,
        )
        _validate_projection_invariant(projection)

    allocation_rationale = [
        f"{a['ticker']} ({a['pct']}%): {a['rationale']}" for a in allocation
    ]

    what_if = None
    if not warnings and override_contribution_monthly is not None and baseline_projection and projection:
        what_if = {
            "baseline_contribution_monthly": recommended_contribution,
            "override_contribution_monthly": effective_contribution,
            "median_delta_end": round(
                projection[-1]["median"] - baseline_projection[-1]["median"], 2
            ),
        }

    outputs = {
        "needs_profile": needs_profile,
        "risk_bucket": bucket,
        "risk_score": score,
        "goal_type": goal_type,
        "target_horizon_months": horizon,
        "monthly_spending_avg": round(float(monthly_spending), 2),
        "emergency_fund_months": emergency_months,
        "investable_monthly": investable,
        "cashflow_positive": cashflow_positive,
        "safety_warnings": warnings,
        "allocation": allocation,
        "projection": projection,
        "gates": gates,
        "risk": {"score": score, "bucket": bucket, "horizon_adjustment": horizon_adj},
        "allocation_rationale": allocation_rationale,
        "assumptions": assumptions,
        "safe_contribution_monthly": tiers["safe_contribution_monthly"] if tiers else None,
        "recommended_contribution_monthly": tiers["recommended_contribution_monthly"] if tiers else None,
        "stretch_contribution_monthly": tiers["stretch_contribution_monthly"] if tiers else None,
        "effective_contribution_monthly": effective_contribution if not warnings else None,
        "why_this_bucket": explain_bucket_choice(score, base_bucket, bucket, goal_reason, goal_type),
        "why_now_or_not_now": explain_now_or_not_now(warnings),
        "downside_note": downside_note_for_bucket(bucket),
        "rebalance_guidance": "Review allocation every 6-12 months or when any sleeve drifts by ~5%+.",
        "unlock_actions": unlock_actions,
        "what_if": what_if,
    }
    run.inputs_snapshot = {
        "score": score, "horizon_months": horizon,
        "liquidity_need": liquidity, "needs_profile": needs_profile,
        "goal_type": goal_type,
        "override_contribution_monthly": override_contribution_monthly,
    }
    run.outputs = outputs

    for item_data in action_items:
        item = RecommendationItem(
            run_id=run.id,
            priority=item_data["priority"],
            type=item_data["type"],
            title=item_data["title"],
            details=item_data.get("details"),
            confidence=Decimal(str(item_data["confidence"])),
        )
        db.add(item)

    await db.commit()
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------

async def list_runs(db: AsyncSession, user_id: uuid.UUID) -> list[RecommendationRun]:
    result = await db.execute(
        select(RecommendationRun)
        .where(RecommendationRun.user_id == user_id)
        .order_by(RecommendationRun.created_at.desc())
    )
    return list(result.scalars().unique().all())


async def get_run(db: AsyncSession, user_id: uuid.UUID, run_id: uuid.UUID) -> RecommendationRun:
    from fastapi import HTTPException, status as http_status
    result = await db.execute(
        select(RecommendationRun).where(
            RecommendationRun.id == run_id,
            RecommendationRun.user_id == user_id,
        )
    )
    run = result.scalars().first()
    if not run:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


async def get_latest_run(db: AsyncSession, user_id: uuid.UUID) -> Optional[RecommendationRun]:
    result = await db.execute(
        select(RecommendationRun)
        .where(RecommendationRun.user_id == user_id)
        .order_by(RecommendationRun.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


def _seed_from_text(seed_text: str) -> int:
    return int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)


async def simulate_what_if(
    db: AsyncSession,
    user_id: uuid.UUID,
    monthly_amount: float,
    goal_type: Optional[GoalType] = None,
    target_horizon_months: Optional[int] = None,
) -> dict:
    from fastapi import HTTPException, status as http_status

    latest = await get_latest_run(db, user_id)
    if not latest or not latest.outputs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="No recommendation run found. Generate a recommendation first.",
        )

    outputs = latest.outputs
    warnings: list[str] = list(outputs.get("safety_warnings", []))
    blocked = len(warnings) > 0

    base_horizon = int(
        (latest.inputs_snapshot or {}).get("horizon_months")
        or outputs.get("target_horizon_months")
        or 60
    )
    horizon = target_horizon_months or base_horizon
    score = int(outputs.get("risk_score") or (latest.inputs_snapshot or {}).get("score") or 50)

    base_bucket = risk_bucket_for_score(score, horizon)
    bucket, _ = apply_goal_mode(base_bucket, goal_type, horizon)
    bucket_cfg = RISK_BUCKETS[bucket]

    base_monthly_amount = float(
        outputs.get("recommended_contribution_monthly")
        or outputs.get("effective_contribution_monthly")
        or outputs.get("investable_monthly")
        or 0.0
    )

    if blocked:
        return {
            "blocked": True,
            "risk_bucket": bucket,
            "goal_type": goal_type,
            "horizon_months": horizon,
            "base_monthly_amount": round(base_monthly_amount, 2),
            "monthly_amount": round(float(monthly_amount), 2),
            "why_now_or_not_now": outputs.get("why_now_or_not_now") or explain_now_or_not_now(warnings),
            "unlock_actions": outputs.get("unlock_actions") or [],
            "downside_note": outputs.get("downside_note") or downside_note_for_bucket(bucket),
            "projection_base": [],
            "projection_override": [],
            "projection_end_base": None,
            "projection_end_override": None,
            "median_delta_end": 0.0,
        }

    initial_balance = float(await _total_balance(db, user_id))
    seed = _seed_from_text(f"{user_id}:{horizon}:{bucket}")

    projection_base = run_projection(
        monthly_contribution=base_monthly_amount,
        initial_balance=initial_balance,
        horizon_months=horizon,
        annual_return=bucket_cfg["return_pct"],
        annual_vol=bucket_cfg["vol_pct"],
        run_seed=seed,
    )
    projection_override = run_projection(
        monthly_contribution=max(float(monthly_amount), 0.0),
        initial_balance=initial_balance,
        horizon_months=horizon,
        annual_return=bucket_cfg["return_pct"],
        annual_vol=bucket_cfg["vol_pct"],
        run_seed=seed,
    )
    _validate_projection_invariant(projection_base)
    _validate_projection_invariant(projection_override)

    end_base = projection_base[-1] if projection_base else None
    end_override = projection_override[-1] if projection_override else None
    delta = 0.0
    if end_base and end_override:
        delta = round(end_override["median"] - end_base["median"], 2)

    return {
        "blocked": False,
        "risk_bucket": bucket,
        "goal_type": goal_type,
        "horizon_months": horizon,
        "base_monthly_amount": round(base_monthly_amount, 2),
        "monthly_amount": round(float(monthly_amount), 2),
        "why_now_or_not_now": outputs.get("why_now_or_not_now") or explain_now_or_not_now([]),
        "unlock_actions": [],
        "downside_note": outputs.get("downside_note") or downside_note_for_bucket(bucket),
        "projection_base": projection_base,
        "projection_override": projection_override,
        "projection_end_base": end_base,
        "projection_end_override": end_override,
        "median_delta_end": delta,
    }
