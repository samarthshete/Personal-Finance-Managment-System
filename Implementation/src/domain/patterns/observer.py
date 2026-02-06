"""
Observer Pattern Implementation
Budget Monitoring System

Defines a one-to-many dependency between objects so that when a
transaction is created, all budget observers are automatically notified.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from decimal import Decimal

from src.domain.entities.models import Transaction, Budget, BudgetAlert, AlertType


class TransactionObserver(ABC):
    """
    Observer Interface
    
    Defines the interface for objects that should be notified
    of transaction events.
    """
    
    @abstractmethod
    def on_transaction_created(self, transaction: Transaction) -> None:
        """Called when a new transaction is created."""
        pass
    
    @abstractmethod
    def on_transaction_updated(self, transaction: Transaction) -> None:
        """Called when a transaction is updated."""
        pass
    
    @abstractmethod
    def on_transaction_deleted(self, transaction: Transaction) -> None:
        """Called when a transaction is deleted."""
        pass


class TransactionSubject:
    """
    Subject (Observable)
    
    Maintains a list of observers and notifies them of transaction events.
    """
    
    def __init__(self):
        self._observers: List[TransactionObserver] = []
    
    def attach(self, observer: TransactionObserver) -> None:
        """Attach an observer to the subject."""
        if observer not in self._observers:
            self._observers.append(observer)
    
    def detach(self, observer: TransactionObserver) -> None:
        """Detach an observer from the subject."""
        if observer in self._observers:
            self._observers.remove(observer)
    
    def notify_created(self, transaction: Transaction) -> None:
        """Notify all observers of a new transaction."""
        for observer in self._observers:
            observer.on_transaction_created(transaction)
    
    def notify_updated(self, transaction: Transaction) -> None:
        """Notify all observers of an updated transaction."""
        for observer in self._observers:
            observer.on_transaction_updated(transaction)
    
    def notify_deleted(self, transaction: Transaction) -> None:
        """Notify all observers of a deleted transaction."""
        for observer in self._observers:
            observer.on_transaction_deleted(transaction)


class BudgetAlertObserver(TransactionObserver):
    """
    Concrete Observer: Budget Alert System
    
    Monitors transactions and checks budget thresholds.
    Creates alerts when thresholds are exceeded.
    """
    
    def __init__(self, budget_repository=None, alert_factory=None, notification_service=None):
        self.budget_repository = budget_repository
        self.alert_factory = alert_factory
        self.notification_service = notification_service
    
    def on_transaction_created(self, transaction: Transaction) -> None:
        """Check budget thresholds when a transaction is created."""
        self._check_budget_threshold(transaction)
    
    def on_transaction_updated(self, transaction: Transaction) -> None:
        """Re-check budget thresholds when a transaction is updated."""
        self._check_budget_threshold(transaction)
    
    def on_transaction_deleted(self, transaction: Transaction) -> None:
        """Re-check budget thresholds when a transaction is deleted."""
        # Could potentially clear alerts if spending drops below threshold
        pass
    
    def _check_budget_threshold(self, transaction: Transaction) -> None:
        """
        Check if the transaction causes budget threshold to be exceeded.
        
        1. Find budget for the transaction's category
        2. Calculate current spending
        3. Check threshold
        4. Create alert if exceeded
        5. Send notification
        """
        if not transaction.category_id:
            return
        
        # Get budget for category
        budget = self._get_budget_for_category(transaction.category_id)
        if not budget:
            return
        
        # Calculate current spending
        current_spending = self._calculate_spending(budget)
        
        # Check threshold
        alert_type = budget.check_threshold(current_spending)
        
        if alert_type:
            # Create alert using Factory Pattern
            alert = self._create_alert(budget, current_spending, alert_type)
            
            # Send notification
            self._send_notification(alert)
    
    def _get_budget_for_category(self, category_id) -> Optional[Budget]:
        """Retrieve budget for the given category."""
        if self.budget_repository:
            return self.budget_repository.find_by_category(category_id)
        return None
    
    def _calculate_spending(self, budget: Budget) -> Decimal:
        """Calculate current spending for the budget period."""
        # Implementation would query transaction repository
        # for sum of transactions in category within budget period
        return Decimal("0.00")
    
    def _create_alert(self, budget: Budget, spending: Decimal, alert_type: AlertType) -> BudgetAlert:
        """Create alert using AlertFactory."""
        if self.alert_factory:
            return self.alert_factory.create_alert(budget, spending, alert_type)
        return BudgetAlert(
            budget_id=budget.budget_id,
            alert_type=alert_type,
            current_spending=spending,
            budget_limit=budget.amount
        )
    
    def _send_notification(self, alert: BudgetAlert) -> None:
        """Send notification to user."""
        if self.notification_service:
            self.notification_service.send_alert(alert)


class AnalyticsCacheObserver(TransactionObserver):
    """
    Concrete Observer: Analytics Cache Invalidation
    
    Invalidates cached analytics data when transactions change.
    """
    
    def __init__(self, cache_service=None):
        self.cache_service = cache_service
    
    def on_transaction_created(self, transaction: Transaction) -> None:
        """Invalidate relevant cache entries."""
        self._invalidate_cache(transaction)
    
    def on_transaction_updated(self, transaction: Transaction) -> None:
        """Invalidate relevant cache entries."""
        self._invalidate_cache(transaction)
    
    def on_transaction_deleted(self, transaction: Transaction) -> None:
        """Invalidate relevant cache entries."""
        self._invalidate_cache(transaction)
    
    def _invalidate_cache(self, transaction: Transaction) -> None:
        """Invalidate cache for affected analytics."""
        if self.cache_service:
            # Invalidate category-specific cache
            if transaction.category_id:
                self.cache_service.invalidate(f"analytics:category:{transaction.category_id}")
            
            # Invalidate account-specific cache
            if transaction.account_id:
                self.cache_service.invalidate(f"analytics:account:{transaction.account_id}")
            
            # Invalidate overall dashboard cache
            self.cache_service.invalidate("analytics:dashboard")
