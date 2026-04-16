from decimal import Decimal
from typing import Optional, List
import uuid

from pydantic import BaseModel


class CategoryTotal(BaseModel):
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    category_type: Optional[str] = None
    total: Decimal


class AccountTotal(BaseModel):
    account_id: str
    total: Decimal


class SummaryResponse(BaseModel):
    total_spending: Decimal
    by_category: List[CategoryTotal]
    by_account: List[AccountTotal]


class TrendPoint(BaseModel):
    period: str
    total: Decimal


class BudgetVsActualItem(BaseModel):
    category_id: str
    limit_amount: Decimal
    spent_amount: Decimal
    percent: Decimal
