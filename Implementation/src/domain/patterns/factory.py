"""
Factory Pattern Implementation
Alert and Report Factories

Define interfaces for creating objects, but let subclasses
decide which classes to instantiate.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from src.domain.entities.models import Budget, BudgetAlert, AlertType


class AlertFactory(ABC):
    """
    Abstract Factory Interface
    
    Defines the interface for creating alert objects.
    """
    
    @abstractmethod
    def create_alert(self, budget: Budget, spending: Decimal, alert_type: AlertType) -> BudgetAlert:
        """Create an alert based on budget and spending."""
        pass


class BudgetAlertFactory(AlertFactory):
    """
    Concrete Factory: Budget Alerts
    
    Creates different types of budget alerts based on threshold severity.
    """
    
    def create_alert(self, budget: Budget, spending: Decimal, alert_type: AlertType) -> BudgetAlert:
        """
        Create appropriate alert based on alert type.
        
        Factory Method: decides which alert message to create.
        """
        if alert_type == AlertType.EXCEEDED:
            return self._create_exceeded_alert(budget, spending)
        elif alert_type == AlertType.WARNING_90:
            return self._create_warning_alert(budget, spending, 90)
        elif alert_type == AlertType.WARNING_80:
            return self._create_warning_alert(budget, spending, 80)
        else:
            return self._create_generic_alert(budget, spending)
    
    def _create_exceeded_alert(self, budget: Budget, spending: Decimal) -> BudgetAlert:
        """Create alert for exceeded budget."""
        overage = spending - budget.amount
        return BudgetAlert(
            alert_id=uuid4(),
            budget_id=budget.budget_id,
            alert_type=AlertType.EXCEEDED,
            message=f"🚨 Budget exceeded! You've spent ${spending:.2f} of your ${budget.amount:.2f} budget (${overage:.2f} over).",
            current_spending=spending,
            budget_limit=budget.amount,
            created_at=datetime.now(),
            is_read=False
        )
    
    def _create_warning_alert(self, budget: Budget, spending: Decimal, percentage: int) -> BudgetAlert:
        """Create warning alert for approaching threshold."""
        remaining = budget.amount - spending
        alert_type = AlertType.WARNING_90 if percentage == 90 else AlertType.WARNING_80
        
        return BudgetAlert(
            alert_id=uuid4(),
            budget_id=budget.budget_id,
            alert_type=alert_type,
            message=f"⚠️ Budget warning: You've used {percentage}% of your budget. ${remaining:.2f} remaining.",
            current_spending=spending,
            budget_limit=budget.amount,
            created_at=datetime.now(),
            is_read=False
        )
    
    def _create_generic_alert(self, budget: Budget, spending: Decimal) -> BudgetAlert:
        """Create generic budget alert."""
        return BudgetAlert(
            alert_id=uuid4(),
            budget_id=budget.budget_id,
            alert_type=AlertType.WARNING_80,
            message=f"Budget update: Current spending is ${spending:.2f} of ${budget.amount:.2f}",
            current_spending=spending,
            budget_limit=budget.amount,
            created_at=datetime.now(),
            is_read=False
        )


# ============================================================================
# Report Factory (Additional Factory Implementation)
# ============================================================================

class Report(ABC):
    """Abstract base class for reports."""
    
    @abstractmethod
    def generate(self) -> bytes:
        """Generate the report content."""
        pass
    
    @abstractmethod
    def get_filename(self) -> str:
        """Get the report filename."""
        pass


class SummaryReport(Report):
    """Summary financial report."""
    
    def __init__(self, data: dict, date_range: tuple):
        self.data = data
        self.date_range = date_range
    
    def generate(self) -> bytes:
        """Generate summary report."""
        # Implementation would create PDF/CSV
        return b"Summary Report Content"
    
    def get_filename(self) -> str:
        return f"summary_report_{self.date_range[0]}_{self.date_range[1]}.pdf"


class DetailedReport(Report):
    """Detailed transaction report."""
    
    def __init__(self, data: dict, date_range: tuple):
        self.data = data
        self.date_range = date_range
    
    def generate(self) -> bytes:
        """Generate detailed report."""
        return b"Detailed Report Content"
    
    def get_filename(self) -> str:
        return f"detailed_report_{self.date_range[0]}_{self.date_range[1]}.csv"


class TaxReport(Report):
    """Tax summary report."""
    
    def __init__(self, data: dict, year: int):
        self.data = data
        self.year = year
    
    def generate(self) -> bytes:
        """Generate tax report."""
        return b"Tax Report Content"
    
    def get_filename(self) -> str:
        return f"tax_report_{self.year}.pdf"


class ReportFactory(ABC):
    """
    Abstract Factory for Reports
    """
    
    @abstractmethod
    def create_report(self, report_type: str, data: dict, **kwargs) -> Report:
        """Create a report of the specified type."""
        pass


class FinancialReportFactory(ReportFactory):
    """
    Concrete Factory: Financial Reports
    
    Creates different types of financial reports.
    """
    
    def create_report(self, report_type: str, data: dict, **kwargs) -> Report:
        """
        Create report based on type.
        
        Factory Method: decides which report class to instantiate.
        """
        if report_type == "summary":
            return SummaryReport(data, kwargs.get("date_range", (None, None)))
        elif report_type == "detailed":
            return DetailedReport(data, kwargs.get("date_range", (None, None)))
        elif report_type == "tax":
            return TaxReport(data, kwargs.get("year", datetime.now().year))
        else:
            raise ValueError(f"Unknown report type: {report_type}")
