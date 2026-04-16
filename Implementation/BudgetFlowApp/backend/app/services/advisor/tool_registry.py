"""
Advisor tool definitions. Each tool is an async function that reads
our DB (user-scoped) and returns a JSON-serializable dict.
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Coroutine, Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import FinancialAccount
from app.models.category import Category
from app.models.transaction import Transaction, Merchant
from app.services import analytics_service, budget_service, alert_service, recommendation_service


ToolFn = Callable[..., Coroutine[Any, Any, dict]]

TOOL_DEFINITIONS: dict[str, dict] = {}
_TOOL_FUNCTIONS: dict[str, ToolFn] = {}


def _serialize(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _parse_date(val: Optional[str]) -> Optional[date]:
    if not val:
        return None
    return datetime.strptime(val, "%Y-%m-%d").date()


def _parse_uuid_list(val: Optional[list]) -> Optional[list[uuid.UUID]]:
    if not val:
        return None
    out: list[uuid.UUID] = []
    for raw in val:
        out.append(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))
    return out


def _month_bounds(ref: date) -> tuple[date, date]:
    start = ref.replace(day=1)
    if ref.month == 12:
        next_start = date(ref.year + 1, 1, 1)
    else:
        next_start = date(ref.year, ref.month + 1, 1)
    end = next_start - timedelta(days=1)
    return start, end


def _window_bounds(date_from: date, date_to: date) -> tuple[date, date]:
    days = (date_to - date_from).days + 1
    prev_end = date_from - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return prev_start, prev_end


async def _category_name_map(db: AsyncSession, category_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not category_ids:
        return {}
    result = await db.execute(select(Category.id, Category.name).where(Category.id.in_(category_ids)))
    return {row[0]: row[1] for row in result.all()}


async def _category_totals(
    db: AsyncSession,
    user_id: uuid.UUID,
    date_from: date,
    date_to: date,
) -> dict[Optional[uuid.UUID], dict[str, Any]]:
    stmt = (
        select(
            Transaction.category_id,
            Category.name,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(
            FinancialAccount.user_id == user_id,
            Transaction.posted_date >= date_from,
            Transaction.posted_date <= date_to,
            Transaction.amount < 0,
        )
        .group_by(Transaction.category_id, Category.name)
    )
    result = await db.execute(stmt)
    out: dict[Optional[uuid.UUID], dict[str, Any]] = {}
    for category_id, category_name, total in result.all():
        out[category_id] = {
            "category_id": category_id,
            "category_name": category_name or "Uncategorized",
            "total": float(total or 0),
        }
    return out


# --- Tool implementations ---------------------------------------------------


async def _get_summary(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    expenses_only = args.get("expenses_only", False)
    if isinstance(expenses_only, str):
        expenses_only = expenses_only.lower() == "true"
    result = await analytics_service.get_summary(
        db, user_id,
        date_from=_parse_date(args.get("date_from")),
        date_to=_parse_date(args.get("date_to")),
        account_ids=_parse_uuid_list(args.get("account_ids")),
        category_ids=_parse_uuid_list(args.get("category_ids")),
        expenses_only=expenses_only,
    )
    return _serialize(result)


async def _get_trends(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    group_by = args.get("group_by", "month")
    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))
    if not date_from or not date_to:
        return {"error": "date_from and date_to are required for trends"}
    result = await analytics_service.get_trends(db, user_id, date_from, date_to, group_by)
    return {"trends": _serialize(result)}


async def _get_budget_vs_actual(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    budget_id_str = args.get("budget_id")
    if not budget_id_str:
        return {"error": "budget_id is required"}
    result = await analytics_service.get_budget_vs_actual(db, user_id, uuid.UUID(budget_id_str))
    return {"budget_vs_actual": _serialize(result)}


async def _list_budgets(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    budgets = await budget_service.list_budgets(
        db, user_id,
        period_from=args.get("period_from"),
        period_to=args.get("period_to"),
    )
    return {"budgets": [
        _serialize({
            "id": str(b.id), "name": b.name,
            "period_start": b.period_start, "period_end": b.period_end,
            "period_type": b.period_type, "item_count": len(b.items),
        })
        for b in budgets
    ]}


async def _get_budget(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    budget_id_str = args.get("budget_id")
    if not budget_id_str:
        return {"error": "budget_id is required"}
    b = await budget_service.get_budget(db, user_id, uuid.UUID(budget_id_str))
    return _serialize({
        "id": str(b.id), "name": b.name,
        "period_start": b.period_start, "period_end": b.period_end,
        "period_type": b.period_type, "thresholds": b.thresholds,
        "items": [{"category_id": str(i.category_id), "limit_amount": i.limit_amount} for i in b.items],
    })


async def _list_alerts(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    is_read = args.get("is_read")
    if isinstance(is_read, str):
        is_read = is_read.lower() == "true"
    alerts = await alert_service.list_alerts(db, user_id, is_read)
    return {"alerts": [
        _serialize({
            "id": str(a.id),
            "threshold_percent": a.threshold_percent,
            "spent_amount": a.spent_amount,
            "limit_amount": a.limit_amount,
            "period_start": a.period_start,
            "period_end": a.period_end,
            "is_read": a.is_read,
        })
        for a in alerts[:20]
    ]}


async def _list_transactions(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    limit = min(int(args.get("limit", 50)), 100)
    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))

    stmt = (
        select(Transaction)
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
        .order_by(Transaction.posted_date.desc())
        .limit(limit)
    )
    if date_from:
        stmt = stmt.where(Transaction.posted_date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.posted_date <= date_to)
    if args.get("account_ids"):
        stmt = stmt.where(Transaction.account_id.in_(_parse_uuid_list(args["account_ids"])))
    if args.get("category_ids"):
        stmt = stmt.where(Transaction.category_id.in_(_parse_uuid_list(args["category_ids"])))

    result = await db.execute(stmt)
    txns = result.scalars().all()
    return {"transactions": [
        _serialize({
            "id": str(t.id), "posted_date": t.posted_date,
            "amount": t.amount, "description": t.description,
            "category_id": str(t.category_id) if t.category_id else None,
        })
        for t in txns
    ], "count": len(txns)}


async def _compare_spending(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    period_a = args.get("period_a") or {}
    period_b = args.get("period_b") or {}
    a_from = _parse_date(period_a.get("date_from"))
    a_to = _parse_date(period_a.get("date_to"))
    b_from = _parse_date(period_b.get("date_from"))
    b_to = _parse_date(period_b.get("date_to"))
    if not all([a_from, a_to, b_from, b_to]):
        return {"error": "period_a.date_from/date_to and period_b.date_from/date_to are required"}

    expenses_only = args.get("expenses_only", True)
    if isinstance(expenses_only, str):
        expenses_only = expenses_only.lower() == "true"

    account_ids = _parse_uuid_list(args.get("account_ids"))
    category_ids = _parse_uuid_list(args.get("category_ids"))

    summary_a = await analytics_service.get_summary(
        db, user_id,
        date_from=a_from,
        date_to=a_to,
        account_ids=account_ids,
        category_ids=category_ids,
        expenses_only=bool(expenses_only),
    )
    summary_b = await analytics_service.get_summary(
        db, user_id,
        date_from=b_from,
        date_to=b_to,
        account_ids=account_ids,
        category_ids=category_ids,
        expenses_only=bool(expenses_only),
    )

    total_a = float(summary_a.get("total_spending") or 0)
    total_b = float(summary_b.get("total_spending") or 0)
    delta = round(total_b - total_a, 2)
    delta_percent = round((delta / total_a) * 100, 2) if total_a > 0 else None

    if abs(delta) < 0.01:
        better_or_worse = "flat"
    elif bool(expenses_only):
        better_or_worse = "better" if delta < 0 else "worse"
    else:
        better_or_worse = "up" if delta > 0 else "down"

    return _serialize({
        "period_a": {"date_from": a_from, "date_to": a_to},
        "period_b": {"date_from": b_from, "date_to": b_to},
        "total_a": total_a,
        "total_b": total_b,
        "delta_amount": delta,
        "delta_percent": delta_percent,
        "better_or_worse": better_or_worse,
        "expenses_only": bool(expenses_only),
    })


async def _top_category_changes(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    current_from = _parse_date(args.get("date_from"))
    current_to = _parse_date(args.get("date_to"))
    if not current_from or not current_to:
        return {"error": "date_from and date_to are required"}

    compare_to_previous = args.get("compare_to_previous", True)
    if isinstance(compare_to_previous, str):
        compare_to_previous = compare_to_previous.lower() == "true"

    previous_from, previous_to = _window_bounds(current_from, current_to)
    current = await _category_totals(db, user_id, current_from, current_to)
    previous = await _category_totals(db, user_id, previous_from, previous_to) if compare_to_previous else {}

    rows: list[dict[str, Any]] = []
    all_keys = set(current.keys()) | set(previous.keys())
    for key in all_keys:
        c = current.get(key, {"category_id": key, "category_name": "Uncategorized", "total": 0.0})
        p = previous.get(key, {"category_id": key, "category_name": c["category_name"], "total": 0.0})
        delta = round(float(c["total"]) - float(p["total"]), 2)
        pct = round((delta / float(p["total"])) * 100, 2) if float(p["total"]) > 0 else None
        direction = "increase" if delta > 0 else "decrease" if delta < 0 else "flat"
        rows.append({
            "category_id": str(c["category_id"]) if c["category_id"] else None,
            "category_name": c["category_name"],
            "total_current": round(float(c["total"]), 2),
            "total_previous": round(float(p["total"]), 2),
            "delta_amount": delta,
            "delta_percent": pct,
            "direction": direction,
        })

    rows.sort(key=lambda r: abs(r["delta_amount"]), reverse=True)
    increases = [r for r in rows if r["delta_amount"] > 0][:5]
    decreases = [r for r in rows if r["delta_amount"] < 0][:5]

    return _serialize({
        "period_current": {"date_from": current_from, "date_to": current_to},
        "period_previous": {"date_from": previous_from, "date_to": previous_to} if compare_to_previous else None,
        "changes": rows[:10],
        "top_increases": increases,
        "top_decreases": decreases,
    })


async def _budget_coaching(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    period = args.get("period")
    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))
    if period == "current_month" or (not date_from and not date_to):
        date_from, date_to = _month_bounds(date.today())
    if not date_from or not date_to:
        return {"error": "Provide period=current_month or date_from/date_to"}

    budgets = await budget_service.list_budgets(
        db, user_id,
        period_from=date_from.isoformat(),
        period_to=date_to.isoformat(),
    )
    if not budgets:
        return {
            "period": {"date_from": date_from, "date_to": date_to},
            "near_threshold": [],
            "over_budget": [],
            "estimated_amount_to_cut": 0.0,
            "note": "No budgets found for this period.",
        }

    near_threshold: list[dict[str, Any]] = []
    over_budget: list[dict[str, Any]] = []
    estimated_cut = 0.0

    today = date.today()

    for b in budgets:
        rows = await analytics_service.get_budget_vs_actual(db, user_id, b.id)
        cat_ids = [uuid.UUID(r["category_id"]) for r in rows]
        names = await _category_name_map(db, cat_ids)

        for r in rows:
            pct = float(r["percent"])
            spent = float(r["spent_amount"])
            limit_amt = float(r["limit_amount"])
            category_id = uuid.UUID(r["category_id"])
            row = {
                "budget_id": str(b.id),
                "budget_name": b.name,
                "category_id": str(category_id),
                "category_name": names.get(category_id, "Uncategorized"),
                "spent_amount": round(spent, 2),
                "limit_amount": round(limit_amt, 2),
                "utilization_percent": round(pct * 100, 2),
            }

            if pct >= 1.0:
                cut = max(spent - limit_amt, 0.0)
                row["estimated_cut"] = round(cut, 2)
                over_budget.append(row)
                estimated_cut += cut
            elif pct >= 0.8:
                cut = 0.0
                if b.period_start <= today <= b.period_end:
                    elapsed_days = max((today - b.period_start).days + 1, 1)
                    total_days = max((b.period_end - b.period_start).days + 1, 1)
                    elapsed_ratio = max(min(elapsed_days / total_days, 1.0), 0.1)
                    projected = spent / elapsed_ratio
                    cut = max(projected - limit_amt, 0.0)
                row["estimated_cut"] = round(cut, 2)
                near_threshold.append(row)
                estimated_cut += cut

    near_threshold.sort(key=lambda x: x["utilization_percent"], reverse=True)
    over_budget.sort(key=lambda x: x["utilization_percent"], reverse=True)

    return _serialize({
        "period": {"date_from": date_from, "date_to": date_to},
        "near_threshold": near_threshold,
        "over_budget": over_budget,
        "estimated_amount_to_cut": round(estimated_cut, 2),
    })


async def _spending_opportunities(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    current_to = _parse_date(args.get("date_to")) or date.today()
    current_from = _parse_date(args.get("date_from")) or (current_to - timedelta(days=89))
    previous_from, previous_to = _window_bounds(current_from, current_to)

    current_categories = await _category_totals(db, user_id, current_from, current_to)
    previous_categories = await _category_totals(db, user_id, previous_from, previous_to)

    discretionary_keywords = (
        "dining", "restaurant", "entertain", "shopping", "travel",
        "coffee", "subscription", "stream", "bar", "takeout",
    )

    category_opportunities: list[dict[str, Any]] = []
    for key, cur in current_categories.items():
        prev = previous_categories.get(key, {"total": 0.0})
        current_total = float(cur["total"])
        previous_total = float(prev["total"])
        delta = current_total - previous_total
        name = str(cur["category_name"])
        discretionary = any(k in name.lower() for k in discretionary_keywords)
        score = current_total + max(delta, 0.0) * 0.5 + (current_total * 0.2 if discretionary else 0.0)
        if current_total <= 0:
            continue
        suggested_cut = max(delta, current_total * (0.12 if discretionary else 0.08))
        category_opportunities.append({
            "category_id": str(cur["category_id"]) if cur["category_id"] else None,
            "category_name": name,
            "current_total": round(current_total, 2),
            "previous_total": round(previous_total, 2),
            "delta_amount": round(delta, 2),
            "discretionary_signal": discretionary,
            "opportunity_score": round(score, 2),
            "suggested_cut": round(suggested_cut, 2),
        })

    category_opportunities.sort(key=lambda x: x["opportunity_score"], reverse=True)

    merchant_stmt = (
        select(
            func.coalesce(Merchant.name, Transaction.description_normalized).label("merchant_name"),
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
            func.count(Transaction.id).label("txn_count"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .outerjoin(Merchant, Merchant.id == Transaction.merchant_id)
        .where(
            FinancialAccount.user_id == user_id,
            Transaction.posted_date >= current_from,
            Transaction.posted_date <= current_to,
            Transaction.amount < 0,
        )
        .group_by("merchant_name")
        .order_by(desc("total"))
        .limit(8)
    )
    merchant_rows = await db.execute(merchant_stmt)

    merchant_opportunities: list[dict[str, Any]] = []
    for merchant_name, total, txn_count in merchant_rows.all():
        total_f = float(total or 0)
        count_i = int(txn_count or 0)
        if total_f <= 0 or count_i <= 0:
            continue
        merchant_opportunities.append({
            "merchant_name": merchant_name or "Unknown merchant",
            "total_spend": round(total_f, 2),
            "transaction_count": count_i,
            "avg_transaction": round(total_f / count_i, 2),
            "suggested_cut": round(total_f * 0.10, 2),
        })

    return _serialize({
        "period": {"date_from": current_from, "date_to": current_to},
        "category_opportunities": category_opportunities[:6],
        "merchant_opportunities": merchant_opportunities[:5],
    })


async def _run_recommendation(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    answers = args.get("answers")
    horizon = int(args.get("horizon_months", 60))
    target_horizon = args.get("target_horizon_months")
    if target_horizon is not None:
        target_horizon = int(target_horizon)
    override_contribution = args.get("override_contribution_monthly")
    if override_contribution is not None:
        override_contribution = float(override_contribution)

    rp_input = None
    if answers and isinstance(answers, dict):
        rp_input = {
            "answers": answers,
            "horizon_months": horizon,
            "liquidity_need": args.get("liquidity_need", "moderate"),
        }

    run = await recommendation_service.execute_run(
        db,
        user_id,
        risk_profile_input=rp_input,
        horizon_override=horizon,
        goal_type=args.get("goal_type"),
        target_horizon_months=target_horizon,
        override_contribution_monthly=override_contribution,
    )
    out = run.outputs or {}

    compact: dict[str, Any] = {
        "run_id": str(run.id),
        "needs_profile": out.get("needs_profile", False),
        "gates": out.get("gates", []),
        "safety_warnings": out.get("safety_warnings", []),
        "risk": out.get("risk"),
        "monthly_spending_avg": out.get("monthly_spending_avg", 0),
        "emergency_fund_months": out.get("emergency_fund_months", 0),
        "investable_monthly": out.get("investable_monthly", 0),
        "cashflow_positive": out.get("cashflow_positive", True),
        "safe_contribution_monthly": out.get("safe_contribution_monthly"),
        "recommended_contribution_monthly": out.get("recommended_contribution_monthly"),
        "stretch_contribution_monthly": out.get("stretch_contribution_monthly"),
        "why_this_bucket": out.get("why_this_bucket"),
        "why_now_or_not_now": out.get("why_now_or_not_now"),
        "downside_note": out.get("downside_note"),
        "rebalance_guidance": out.get("rebalance_guidance"),
        "unlock_actions": out.get("unlock_actions", []),
    }

    blocked = len(out.get("safety_warnings", [])) > 0

    if not blocked:
        compact["allocation"] = [
            {"ticker": a["ticker"], "pct": a["pct"]} for a in out.get("allocation", [])
        ]
        compact["allocation_rationale"] = out.get("allocation_rationale", [])
        proj = out.get("projection", [])
        if proj:
            compact["projection_start"] = proj[0]
            compact["projection_end"] = proj[-1]
            compact["projection_points"] = len(proj)
    else:
        items = [{"priority": i.priority, "type": i.type, "title": i.title} for i in run.items]
        compact["action_items"] = items

    if out.get("needs_profile"):
        compact["missing_profile_note"] = (
            "No risk profile on file. Ask the user these 5 questions (each 1-5): "
            "market_drop_reaction, investment_experience, income_stability, "
            "loss_tolerance_pct, goal_priority. Also ask for horizon_months."
        )

    return _serialize(compact)


async def _get_latest_recommendation(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    run = await recommendation_service.get_latest_run(db, user_id)
    if not run:
        return {"error": "No recommendation runs found. Use run_recommendation first."}

    out = run.outputs or {}
    blocked = len(out.get("safety_warnings", [])) > 0

    compact: dict[str, Any] = {
        "run_id": str(run.id),
        "created_at": run.created_at,
        "needs_profile": out.get("needs_profile", False),
        "gates": out.get("gates", []),
        "risk": out.get("risk"),
        "monthly_spending_avg": out.get("monthly_spending_avg", 0),
        "emergency_fund_months": out.get("emergency_fund_months", 0),
        "investable_monthly": out.get("investable_monthly", 0),
        "cashflow_positive": out.get("cashflow_positive", True),
        "safety_warnings": out.get("safety_warnings", []),
        "safe_contribution_monthly": out.get("safe_contribution_monthly"),
        "recommended_contribution_monthly": out.get("recommended_contribution_monthly"),
        "stretch_contribution_monthly": out.get("stretch_contribution_monthly"),
        "why_this_bucket": out.get("why_this_bucket"),
        "why_now_or_not_now": out.get("why_now_or_not_now"),
        "downside_note": out.get("downside_note"),
        "rebalance_guidance": out.get("rebalance_guidance"),
        "unlock_actions": out.get("unlock_actions", []),
    }

    if not blocked:
        compact["allocation"] = [
            {"ticker": a["ticker"], "pct": a["pct"]} for a in out.get("allocation", [])
        ]
        proj = out.get("projection", [])
        if proj:
            compact["projection_end"] = proj[-1]

    items = [{"priority": i.priority, "type": i.type, "title": i.title} for i in run.items]
    compact["action_items"] = items
    return _serialize(compact)


async def _explain_latest_recommendation(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    run = await recommendation_service.get_latest_run(db, user_id)
    if not run:
        return {"error": "No recommendation runs found. Use run_recommendation first."}

    out = run.outputs or {}
    blocked = len(out.get("safety_warnings", [])) > 0

    payload: dict[str, Any] = {
        "run_id": str(run.id),
        "blocked": blocked,
        "risk_bucket": out.get("risk_bucket"),
        "why_this_bucket": out.get("why_this_bucket"),
        "why_now_or_not_now": out.get("why_now_or_not_now"),
        "downside_note": out.get("downside_note"),
        "rebalance_guidance": out.get("rebalance_guidance"),
        "unlock_actions": out.get("unlock_actions", []),
        "safe_contribution_monthly": out.get("safe_contribution_monthly"),
        "recommended_contribution_monthly": out.get("recommended_contribution_monthly"),
        "stretch_contribution_monthly": out.get("stretch_contribution_monthly"),
        "gates": out.get("gates", []),
        "safety_warnings": out.get("safety_warnings", []),
    }

    if not blocked:
        payload["allocation"] = [
            {"asset": a.get("asset"), "ticker": a.get("ticker"), "pct": a.get("pct")}
            for a in out.get("allocation", [])
        ]
        proj = out.get("projection", [])
        if proj:
            payload["projection_end"] = proj[-1]

    return _serialize(payload)


async def _simulate_investment_change(db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    monthly_amount = args.get("monthly_amount")
    if monthly_amount is None:
        return {"error": "monthly_amount is required"}

    sim = await recommendation_service.simulate_what_if(
        db,
        user_id,
        monthly_amount=float(monthly_amount),
        goal_type=args.get("goal_type"),
        target_horizon_months=int(args.get("horizon") or args.get("target_horizon_months")) if (args.get("horizon") or args.get("target_horizon_months")) else None,
    )
    return _serialize(sim)


# --- Registry ---------------------------------------------------------------


_TOOL_FUNCTIONS = {
    "get_summary": _get_summary,
    "get_trends": _get_trends,
    "get_budget_vs_actual": _get_budget_vs_actual,
    "list_budgets": _list_budgets,
    "get_budget": _get_budget,
    "list_alerts": _list_alerts,
    "list_transactions": _list_transactions,
    "compare_spending": _compare_spending,
    "top_category_changes": _top_category_changes,
    "budget_coaching": _budget_coaching,
    "spending_opportunities": _spending_opportunities,
    "run_recommendation": _run_recommendation,
    "get_latest_recommendation": _get_latest_recommendation,
    "explain_latest_recommendation": _explain_latest_recommendation,
    "simulate_investment_change": _simulate_investment_change,
}

TOOL_DEFINITIONS = {
    "get_summary": {
        "name": "get_summary",
        "description": (
            "Get spending summary with totals broken down by category (with names) and account. "
            "Pass expenses_only=true when answering spending or category questions so that "
            "income/salary transactions are excluded. "
            "The by_category list is sorted by total descending (top spender first). "
            "Supports optional date range and filters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                "expenses_only": {
                    "type": "boolean",
                    "description": (
                        "When true, only debit/expense transactions (amount < 0) are counted. "
                        "Always set to true for spending or category-breakdown questions."
                    ),
                },
                "account_ids": {"type": "array", "items": {"type": "string"}, "description": "Filter by account UUIDs"},
                "category_ids": {"type": "array", "items": {"type": "string"}, "description": "Filter by category UUIDs"},
            },
        },
    },
    "get_trends": {
        "name": "get_trends",
        "description": "Get spending trends over time grouped by day, week, or month. Requires date_from and date_to.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                "group_by": {"type": "string", "enum": ["day", "week", "month"], "description": "Grouping period"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    "compare_spending": {
        "name": "compare_spending",
        "description": "Compare total spending between two explicit periods. Returns deltas and better/worse classification.",
        "parameters": {
            "type": "object",
            "properties": {
                "period_a": {
                    "type": "object",
                    "properties": {
                        "date_from": {"type": "string"},
                        "date_to": {"type": "string"},
                    },
                    "required": ["date_from", "date_to"],
                },
                "period_b": {
                    "type": "object",
                    "properties": {
                        "date_from": {"type": "string"},
                        "date_to": {"type": "string"},
                    },
                    "required": ["date_from", "date_to"],
                },
                "account_ids": {"type": "array", "items": {"type": "string"}},
                "category_ids": {"type": "array", "items": {"type": "string"}},
                "expenses_only": {"type": "boolean", "default": True},
            },
            "required": ["period_a", "period_b"],
        },
    },
    "top_category_changes": {
        "name": "top_category_changes",
        "description": "Show categories with the largest spending increase/decrease for a period versus the previous period.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Current period start YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Current period end YYYY-MM-DD"},
                "compare_to_previous": {"type": "boolean", "default": True},
            },
            "required": ["date_from", "date_to"],
        },
    },
    "budget_coaching": {
        "name": "budget_coaching",
        "description": "Summarize near-threshold and over-budget categories and estimate required cuts to stay on track.",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["current_month"]},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
    "spending_opportunities": {
        "name": "spending_opportunities",
        "description": "Find categories and merchants with elevated/discretionary spend where reductions are most impactful.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
        },
    },
    "get_budget_vs_actual": {
        "name": "get_budget_vs_actual",
        "description": "Compare actual spending against budget limits per category for a specific budget.",
        "parameters": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget UUID"},
            },
            "required": ["budget_id"],
        },
    },
    "list_budgets": {
        "name": "list_budgets",
        "description": "List the user's budgets with optional period filter.",
        "parameters": {
            "type": "object",
            "properties": {
                "period_from": {"type": "string", "description": "Filter budgets overlapping this start date YYYY-MM-DD"},
                "period_to": {"type": "string", "description": "Filter budgets overlapping this end date YYYY-MM-DD"},
            },
        },
    },
    "get_budget": {
        "name": "get_budget",
        "description": "Get details of a specific budget including items and thresholds.",
        "parameters": {
            "type": "object",
            "properties": {
                "budget_id": {"type": "string", "description": "Budget UUID"},
            },
            "required": ["budget_id"],
        },
    },
    "list_alerts": {
        "name": "list_alerts",
        "description": "List budget alerts (threshold breach notifications). Optionally filter by read status.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_read": {"type": "boolean", "description": "Filter by read status"},
            },
        },
    },
    "list_transactions": {
        "name": "list_transactions",
        "description": "List recent transactions with optional date range and filters. Returns up to 50 rows.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                "account_ids": {"type": "array", "items": {"type": "string"}, "description": "Filter by account UUIDs"},
                "category_ids": {"type": "array", "items": {"type": "string"}, "description": "Filter by category UUIDs"},
                "limit": {"type": "integer", "description": "Max results (default 50, max 100)"},
            },
        },
    },
    "run_recommendation": {
        "name": "run_recommendation",
        "description": "Run the investment recommendation engine. Returns safety gates, risk bucket, contribution tiers, allocation, and projection.",
        "parameters": {
            "type": "object",
            "properties": {
                "horizon_months": {"type": "integer", "description": "Investment horizon in months (6-360). Default 60."},
                "goal_type": {"type": "string", "enum": ["retirement", "house", "emergency", "general"]},
                "target_horizon_months": {"type": "integer", "description": "Optional goal-specific horizon override."},
                "override_contribution_monthly": {"type": "number", "description": "Optional what-if contribution override."},
                "answers": {
                    "type": "object",
                    "description": "Risk profile answers (each 1-5): market_drop_reaction, investment_experience, income_stability, loss_tolerance_pct, goal_priority. Omit if unknown.",
                    "properties": {
                        "market_drop_reaction": {"type": "integer"},
                        "investment_experience": {"type": "integer"},
                        "income_stability": {"type": "integer"},
                        "loss_tolerance_pct": {"type": "integer"},
                        "goal_priority": {"type": "integer"},
                    },
                },
                "liquidity_need": {"type": "string", "enum": ["low", "moderate", "high"], "description": "How soon user may need cash"},
            },
        },
    },
    "get_latest_recommendation": {
        "name": "get_latest_recommendation",
        "description": "Get the most recent recommendation run results including gates, tiers, explanations, allocation, and action items.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "explain_latest_recommendation": {
        "name": "explain_latest_recommendation",
        "description": "Explain why the latest recommendation bucket was chosen, why investing is allowed or blocked, and what to do next.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "simulate_investment_change": {
        "name": "simulate_investment_change",
        "description": "Run deterministic what-if projection for a new monthly investment amount using latest recommendation context.",
        "parameters": {
            "type": "object",
            "properties": {
                "monthly_amount": {"type": "number"},
                "goal_type": {"type": "string", "enum": ["retirement", "house", "emergency", "general"]},
                "horizon": {"type": "integer", "description": "Optional horizon months"},
                "target_horizon_months": {"type": "integer", "description": "Alias for horizon months"},
            },
            "required": ["monthly_amount"],
        },
    },
}


async def execute_tool(name: str, db: AsyncSession, user_id: uuid.UUID, args: dict) -> dict:
    fn = _TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await fn(db, user_id, args)
    except Exception as exc:
        return {"error": str(exc)[:300]}
