import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import FinancialAccount
from app.models.budget import Budget, BudgetItem
from app.models.transaction import Transaction
from app.models.report import Report
from app.renderers import csv_renderer, pdf_renderer
from app.services import job_service
from app.storage import ReportStorage


def _apply_filters(stmt, from_date, to_date, account_ids=None, category_ids=None):
    stmt = stmt.where(Transaction.posted_date >= from_date, Transaction.posted_date <= to_date)
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if category_ids:
        stmt = stmt.where(Transaction.category_id.in_(category_ids))
    return stmt


async def _fetch_summary(db: AsyncSession, user_id: uuid.UUID, from_date, to_date, account_ids=None, category_ids=None) -> dict:
    base = (
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
    )
    total_stmt = _apply_filters(base, from_date, to_date, account_ids, category_ids)
    total = (await db.execute(total_stmt)).scalar() or Decimal("0")

    by_cat_stmt = (
        select(Transaction.category_id, func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
        .group_by(Transaction.category_id)
    )
    by_cat_stmt = _apply_filters(by_cat_stmt, from_date, to_date, account_ids, category_ids)
    cat_rows = (await db.execute(by_cat_stmt)).all()

    by_acct_stmt = (
        select(Transaction.account_id, func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"))
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
        .group_by(Transaction.account_id)
    )
    by_acct_stmt = _apply_filters(by_acct_stmt, from_date, to_date, account_ids, category_ids)
    acct_rows = (await db.execute(by_acct_stmt)).all()

    return {
        "total_spending": total,
        "by_category": [{"category_id": str(r[0]) if r[0] else None, "total": r[1]} for r in cat_rows],
        "by_account": [{"account_id": str(r[0]), "total": r[1]} for r in acct_rows],
    }


async def _fetch_transactions(db: AsyncSession, user_id: uuid.UUID, from_date, to_date, account_ids=None, category_ids=None) -> list:
    stmt = (
        select(Transaction)
        .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
        .where(FinancialAccount.user_id == user_id)
        .order_by(Transaction.posted_date.desc())
    )
    stmt = _apply_filters(stmt, from_date, to_date, account_ids, category_ids)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "posted_date": str(t.posted_date),
            "description": t.description,
            "amount": t.amount,
            "currency": t.currency,
            "category_id": str(t.category_id) if t.category_id else "",
            "account_id": str(t.account_id),
        }
        for t in rows
    ]


async def _fetch_budget_vs_actual(db: AsyncSession, user_id: uuid.UUID, from_date, to_date) -> list:
    budget_stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.period_start >= from_date,
        Budget.period_end <= to_date,
    )
    budgets = (await db.execute(budget_stmt)).scalars().all()
    if not budgets:
        return []

    rows = []
    for budget in budgets:
        if not budget.items:
            continue
        cat_ids = [item.category_id for item in budget.items]
        limit_map = {item.category_id: item.limit_amount for item in budget.items}

        spent_stmt = (
            select(Transaction.category_id, func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("spent"))
            .join(FinancialAccount, FinancialAccount.id == Transaction.account_id)
            .where(
                FinancialAccount.user_id == user_id,
                Transaction.category_id.in_(cat_ids),
                Transaction.posted_date >= budget.period_start,
                Transaction.posted_date <= budget.period_end,
            )
            .group_by(Transaction.category_id)
        )
        spent_map = {r[0]: r[1] for r in (await db.execute(spent_stmt)).all()}

        for cat_id in cat_ids:
            limit_amt = limit_map[cat_id]
            spent_amt = spent_map.get(cat_id, Decimal("0"))
            pct = (spent_amt / limit_amt) if limit_amt > 0 else Decimal("0")
            rows.append({
                "category_id": str(cat_id),
                "limit_amount": limit_amt,
                "spent_amount": spent_amt,
                "percent": round(pct, 4),
            })
    return rows


def _render(report_type: str, fmt: str, data, from_date: str, to_date: str) -> tuple[bytes, str]:
    if fmt == "csv":
        renderers = {
            "monthly_summary": lambda: csv_renderer.render_monthly_summary(data),
            "category_breakdown": lambda: csv_renderer.render_category_breakdown(data),
            "budget_vs_actual": lambda: csv_renderer.render_budget_vs_actual(data),
            "transactions": lambda: csv_renderer.render_transactions(data),
        }
        return renderers[report_type](), "text/csv"
    else:
        renderers = {
            "monthly_summary": lambda: pdf_renderer.render_monthly_summary(data, from_date, to_date),
            "category_breakdown": lambda: pdf_renderer.render_category_breakdown(data, from_date, to_date),
            "budget_vs_actual": lambda: pdf_renderer.render_budget_vs_actual(data, from_date, to_date),
            "transactions": lambda: pdf_renderer.render_transactions(data, from_date, to_date),
        }
        return renderers[report_type](), "application/pdf"


async def _do_generate(
    db: AsyncSession,
    report: Report,
    storage: ReportStorage,
) -> None:
    """Generate report file and update report row. Modifies report in place."""
    report_type = report.type
    fmt = report.format
    from_date = report.from_date
    to_date = report.to_date
    user_id = report.user_id
    filters = report.filters_json
    account_ids = filters.get("account_ids") if filters else None
    category_ids = filters.get("category_ids") if filters else None

    try:
        if report_type == "monthly_summary":
            data = await _fetch_summary(db, user_id, from_date, to_date, account_ids, category_ids)
        elif report_type == "category_breakdown":
            summary = await _fetch_summary(db, user_id, from_date, to_date, account_ids, category_ids)
            data = summary["by_category"]
        elif report_type == "budget_vs_actual":
            data = await _fetch_budget_vs_actual(db, user_id, from_date, to_date)
        elif report_type == "transactions":
            data = await _fetch_transactions(db, user_id, from_date, to_date, account_ids, category_ids)
        else:
            raise ValueError(f"Unknown report type: {report_type}")

        file_bytes, content_type = _render(report_type, fmt, data, str(from_date), str(to_date))

        ext = "csv" if fmt == "csv" else "pdf"
        storage_key = f"reports/{user_id}/{report.id}.{ext}"
        await storage.put(storage_key, file_bytes, content_type)

        report.storage_key = storage_key
        report.status = "succeeded"
        report.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        report.status = "failed"
        report.error = str(exc)[:500]
        report.completed_at = datetime.now(timezone.utc)
        raise


async def generate_report_by_id(
    db: AsyncSession,
    report_id: uuid.UUID,
    storage: ReportStorage,
) -> dict:
    """
    Generate report content for an existing Report row. Callable from worker.
    Returns result dict for job. Raises on error.
    """
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalars().first()
    if not report:
        raise ValueError("Report not found")
    await _do_generate(db, report, storage)
    # Do not commit here: the worker session must remain open so the same Job
    # instance can be updated by job_service.mark_succeeded/mark_failed. An
    # inner commit expires ORM state and breaks refresh() on the Job row.
    await db.flush()
    return {"report_id": str(report.id), "storage_key": report.storage_key, "status": report.status}


async def create_report_async(
    db: AsyncSession,
    user_id: uuid.UUID,
    report_type: str,
    fmt: str,
    from_date,
    to_date,
    filters: Optional[dict],
) -> Report:
    """Create report and enqueue job. Returns report with job_id. Caller commits."""
    report = Report(
        user_id=user_id,
        type=report_type,
        format=fmt,
        from_date=from_date,
        to_date=to_date,
        filters_json=filters,
        status="queued",
    )
    db.add(report)
    await db.flush()
    payload = {
        "report_id": str(report.id),
        "type": report_type,
        "format": fmt,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "filters": filters,
    }
    job = await job_service.create_job_in_session(db, user_id, "report.generate", payload)
    report.job_id = job.id
    await db.commit()
    await db.refresh(report)
    return report


async def create_report(
    db: AsyncSession,
    user_id: uuid.UUID,
    report_type: str,
    fmt: str,
    from_date,
    to_date,
    filters: Optional[dict],
    storage: ReportStorage,
) -> Report:
    """Synchronous create + generate (kept for backward compat / tests)."""
    report = Report(
        user_id=user_id,
        type=report_type,
        format=fmt,
        from_date=from_date,
        to_date=to_date,
        filters_json=filters,
        status="running",
    )
    db.add(report)
    await db.flush()
    await _do_generate(db, report, storage)
    await db.commit()
    await db.refresh(report)
    return report


async def list_reports(db: AsyncSession, user_id: uuid.UUID) -> List[Report]:
    result = await db.execute(
        select(Report)
        .where(Report.user_id == user_id)
        .order_by(Report.created_at.desc())
    )
    return list(result.scalars().all())


async def get_report(db: AsyncSession, user_id: uuid.UUID, report_id: uuid.UUID) -> Report:
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.user_id == user_id)
    )
    report = result.scalars().first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return report
