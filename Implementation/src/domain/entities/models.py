"""
Domain Entities
Intelligent Personal Finance Management System

Implements core domain objects with ORM mapping.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4


# ============================================================================
# ENUMERATIONS
# ============================================================================

class AccountType(Enum):
    """Types of financial accounts."""
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment"


class AccountState(Enum):
    """Account lifecycle states (State Pattern)."""
    PENDING = "pending"
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"
    OVERDRAWN = "overdrawn"


class ConfidenceLevel(Enum):
    """Categorization confidence levels."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    USER_VERIFIED = "user_verified"


class BudgetPeriod(Enum):
    """Budget time periods."""
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RuleType(Enum):
    """Types of categorization rules (Strategy Pattern)."""
    KEYWORD = "keyword"
    MERCHANT = "merchant"
    AMOUNT_RANGE = "amount_range"


class AlertType(Enum):
    """Budget alert severity types."""
    WARNING_80 = "warning_80"
    WARNING_90 = "warning_90"
    EXCEEDED = "exceeded"


# ============================================================================
# DOMAIN ENTITIES
# ============================================================================

@dataclass
class User:
    """User entity - owns accounts, budgets, and rules."""
    user_id: UUID = field(default_factory=uuid4)
    email: str = ""
    password_hash: str = ""
    name: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    
    def authenticate(self, password: str) -> bool:
        """Verify user password."""
        # Implementation would use bcrypt
        pass


@dataclass
class Account:
    """Financial account entity with State Pattern."""
    account_id: UUID = field(default_factory=uuid4)
    user_id: UUID = None
    name: str = ""
    account_type: AccountType = AccountType.CHECKING
    balance: Decimal = Decimal("0.00")
    state: AccountState = AccountState.ACTIVE
    created_at: datetime = field(default_factory=datetime.now)
    
    def deposit(self, amount: Decimal) -> None:
        """Deposit money into account."""
        if self.state != AccountState.ACTIVE:
            raise ValueError(f"Cannot deposit to {self.state.value} account")
        self.balance += amount
    
    def withdraw(self, amount: Decimal) -> None:
        """Withdraw money from account."""
        if self.state != AccountState.ACTIVE:
            raise ValueError(f"Cannot withdraw from {self.state.value} account")
        self.balance -= amount
        if self.balance < 0:
            self.state = AccountState.OVERDRAWN
    
    def freeze(self) -> None:
        """Freeze the account."""
        self.state = AccountState.FROZEN
    
    def unfreeze(self) -> None:
        """Unfreeze the account."""
        if self.state == AccountState.FROZEN:
            self.state = AccountState.ACTIVE


@dataclass
class Transaction:
    """Financial transaction entity."""
    transaction_id: UUID = field(default_factory=uuid4)
    account_id: UUID = None
    amount: Decimal = Decimal("0.00")
    description: str = ""
    merchant_name: str = ""
    category_id: UUID = None
    transaction_date: datetime = field(default_factory=datetime.now)
    is_recurring: bool = False
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    categorization_method: str = ""  # "rule", "ai", "manual"
    
    def is_expense(self) -> bool:
        """Check if transaction is an expense."""
        return self.amount < 0
    
    def is_income(self) -> bool:
        """Check if transaction is income."""
        return self.amount > 0
    
    def categorize(self, category_id: UUID, confidence: ConfidenceLevel, method: str) -> None:
        """Assign category to transaction."""
        self.category_id = category_id
        self.confidence = confidence
        self.categorization_method = method


@dataclass
class Category:
    """Transaction category with hierarchical support."""
    category_id: UUID = field(default_factory=uuid4)
    name: str = ""
    parent_category_id: Optional[UUID] = None
    icon: str = ""
    color: str = ""
    is_system: bool = False


@dataclass
class Budget:
    """Budget entity with threshold monitoring (Observer Pattern)."""
    budget_id: UUID = field(default_factory=uuid4)
    user_id: UUID = None
    category_id: UUID = None
    amount: Decimal = Decimal("0.00")
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    alert_threshold: Decimal = Decimal("0.80")  # 80%
    start_date: datetime = field(default_factory=datetime.now)
    
    def check_threshold(self, current_spending: Decimal) -> Optional['AlertType']:
        """Check if spending has exceeded thresholds."""
        if self.amount <= 0:
            return None
        
        percentage = current_spending / self.amount
        
        if percentage >= Decimal("1.0"):
            return AlertType.EXCEEDED
        elif percentage >= Decimal("0.9"):
            return AlertType.WARNING_90
        elif percentage >= self.alert_threshold:
            return AlertType.WARNING_80
        return None
    
    def get_remaining_amount(self, current_spending: Decimal) -> Decimal:
        """Get remaining budget amount."""
        return self.amount - current_spending


@dataclass
class CategorizationRule:
    """Rule for automatic categorization (Strategy Pattern)."""
    rule_id: UUID = field(default_factory=uuid4)
    user_id: UUID = None
    rule_name: str = ""
    rule_type: RuleType = RuleType.KEYWORD
    pattern: str = ""
    category_id: UUID = None
    priority: int = 0
    is_active: bool = True
    
    def matches(self, transaction: Transaction) -> bool:
        """Check if rule matches transaction."""
        if self.rule_type == RuleType.KEYWORD:
            return self.pattern.lower() in transaction.description.lower()
        elif self.rule_type == RuleType.MERCHANT:
            return self.pattern.lower() in transaction.merchant_name.lower()
        return False


@dataclass
class BudgetAlert:
    """Budget threshold alert (Factory Pattern product)."""
    alert_id: UUID = field(default_factory=uuid4)
    budget_id: UUID = None
    alert_type: AlertType = AlertType.WARNING_80
    message: str = ""
    current_spending: Decimal = Decimal("0.00")
    budget_limit: Decimal = Decimal("0.00")
    created_at: datetime = field(default_factory=datetime.now)
    is_read: bool = False
