import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select, func, cast, Date, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import FinancialAccount
from app.models.budget import Budget, BudgetItem
from app.models.category import Category
from app.models.transaction import Transaction


def _base_expense_query(user_id: uuid.UUID):
    """Reusable base: user-isolated transactions joined via account."""
    return (
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
    )


def _apply_filters(stmt, date_from=None, date_to=None, account_ids=None, category_ids=None):
    if date_from:
        stmt = stmt.where(Transaction.posted_date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.posted_date <= date_to)
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if category_ids:
        stmt = stmt.where(Transaction.category_id.in_(category_ids))
    return stmt


async def get_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    account_ids: Optional[List[uuid.UUID]] = None,
    category_ids: Optional[List[uuid.UUID]] = None,
    expenses_only: bool = False,
) -> dict:
    """Return spending summary with optional expense-only filtering.

    When *expenses_only* is True only transactions with amount < 0 (debits)
    are included.  Income / salary transactions are excluded from every
    sub-query, which prevents positive-amount entries from dominating the
    category breakdown and eliminates false "Miscellaneous" readings.

    The by_category list is returned sorted descending by total so the
    caller/LLM sees the top category first.
    """

    def _expense_filter(stmt):
        if expenses_only:
            return stmt.where(Transaction.amount < 0)
        return stmt

    # ── total spending ───────────────────────────────────────────────────
    total_stmt = _base_expense_query(user_id)
    total_stmt = _apply_filters(total_stmt, date_from, date_to, account_ids, category_ids)
    total_stmt = _expense_filter(total_stmt)
    total_result = await db.execute(total_stmt)
    total_spending = total_result.scalar() or Decimal("0")

    # ── by category — with name + type, sorted by total descending ───────
    by_cat_stmt = (
        select(
            Transaction.category_id,
            Category.name.label("category_name"),
            Category.type.label("category_type"),
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(FinancialAccount.user_id == user_id)
        .group_by(Transaction.category_id, Category.name, Category.type)
        .order_by(desc("total"))
    )
    by_cat_stmt = _apply_filters(by_cat_stmt, date_from, date_to, account_ids, category_ids)
    by_cat_stmt = _expense_filter(by_cat_stmt)
    cat_result = await db.execute(by_cat_stmt)
    by_category = [
        {
            "category_id":   str(row[0]) if row[0] else None,
            "category_name": row[1] if row[1] else "Uncategorized",
            "category_type": row[2],
            "total":         row[3],
        }
        for row in cat_result.all()
    ]

    # ── by account ───────────────────────────────────────────────────────
    by_acct_stmt = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
        .group_by(Transaction.account_id)
    )
    by_acct_stmt = _apply_filters(by_acct_stmt, date_from, date_to, account_ids, category_ids)
    by_acct_stmt = _expense_filter(by_acct_stmt)
    acct_result = await db.execute(by_acct_stmt)
    by_account = [
        {"account_id": str(row[0]), "total": row[1]}
        for row in acct_result.all()
    ]

    return {
        "total_spending": total_spending,
        "by_category":    by_category,
        "by_account":     by_account,
    }


async def get_trends(
    db: AsyncSession,
    user_id: uuid.UUID,
    date_from: date,
    date_to: date,
    group_by: str = "month",
    expenses_only: bool = False,
) -> list:
    period_expr = cast(func.date_trunc(group_by, Transaction.posted_date), Date).label("period")

    conditions = [
        FinancialAccount.user_id == user_id,
        Transaction.posted_date >= date_from,
        Transaction.posted_date <= date_to,
    ]
    if expenses_only:
        conditions.append(Transaction.amount < 0)

    stmt = (
        select(
            period_expr,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(*conditions)
        .group_by(period_expr)
        .order_by(period_expr)
    )
    result = await db.execute(stmt)
    return [{"period": row[0].isoformat(), "total": row[1]} for row in result.all()]


async def get_budget_vs_actual(
    db: AsyncSession,
    user_id: uuid.UUID,
    budget_id: uuid.UUID,
) -> list:
    budget_result = await db.execute(
        select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    budget = budget_result.scalars().first()
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found.")

    rows = []
    if not budget.items:
        return rows

    cat_ids = [item.category_id for item in budget.items]
    limit_map: Dict[uuid.UUID, Decimal] = {item.category_id: item.limit_amount for item in budget.items}

    spent_stmt = (
        select(
            Transaction.category_id,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("spent"),
        )
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(
            FinancialAccount.user_id == user_id,
            Transaction.category_id.in_(cat_ids),
            Transaction.posted_date >= budget.period_start,
            Transaction.posted_date <= budget.period_end,
        )
        .group_by(Transaction.category_id)
    )
    spent_result = await db.execute(spent_stmt)
    spent_map = {row[0]: row[1] for row in spent_result.all()}

    for cat_id in cat_ids:
        limit_amt = limit_map[cat_id]
        spent_amt = spent_map.get(cat_id, Decimal("0"))
        pct = (spent_amt / limit_amt) if limit_amt > 0 else Decimal("0")
        rows.append({
            "category_id":   str(cat_id),
            "limit_amount":  limit_amt,
            "spent_amount":  spent_amt,
            "percent":       round(pct, 4),
        })

    return rows
