"""
Unit Tests for Categorization Patterns

Tests for Strategy, Chain of Responsibility, Observer, and Factory patterns.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

# Import domain entities
from src.domain.entities.models import (
    Transaction, Category, Budget, BudgetAlert,
    ConfidenceLevel, AlertType, BudgetPeriod
)

# Import patterns
from src.domain.patterns.strategy import (
    KeywordStrategy, MerchantStrategy, CategoryResult
)
from src.domain.patterns.chain import (
    RuleBasedHandler, AIHandler, ManualHandler, CategorizationChainBuilder
)
from src.domain.patterns.observer import (
    TransactionSubject, BudgetAlertObserver
)
from src.domain.patterns.factory import (
    BudgetAlertFactory
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_transaction():
    """Create a sample transaction for testing."""
    return Transaction(
        transaction_id=uuid4(),
        account_id=uuid4(),
        amount=Decimal("-50.00"),
        description="STARBUCKS COFFEE #1234",
        merchant_name="Starbucks",
        transaction_date=None
    )


@pytest.fixture
def sample_budget():
    """Create a sample budget for testing."""
    return Budget(
        budget_id=uuid4(),
        user_id=uuid4(),
        category_id=uuid4(),
        amount=Decimal("500.00"),
        period=BudgetPeriod.MONTHLY,
        alert_threshold=Decimal("0.80")
    )


# ============================================================================
# STRATEGY PATTERN TESTS
# ============================================================================

class TestKeywordStrategy:
    """Tests for KeywordStrategy."""
    
    def test_keyword_match(self, sample_transaction):
        """Test that keyword matching works correctly."""
        strategy = KeywordStrategy()
        category_id = str(uuid4())
        strategy.add_keyword("starbucks", category_id)
        
        result = strategy.categorize(sample_transaction)
        
        assert result is not None
        assert result.category_id == category_id
        assert result.confidence == ConfidenceLevel.HIGH
    
    def test_no_keyword_match(self, sample_transaction):
        """Test that no match returns None."""
        strategy = KeywordStrategy()
        strategy.add_keyword("walmart", str(uuid4()))
        
        result = strategy.categorize(sample_transaction)
        
        assert result is None


class TestMerchantStrategy:
    """Tests for MerchantStrategy."""
    
    def test_merchant_match(self, sample_transaction):
        """Test that merchant matching works correctly."""
        strategy = MerchantStrategy()
        category_id = str(uuid4())
        strategy.add_merchant("starbucks", category_id)
        
        result = strategy.categorize(sample_transaction)
        
        assert result is not None
        assert result.category_id == category_id


# ============================================================================
# CHAIN OF RESPONSIBILITY TESTS
# ============================================================================

class TestCategorizationChain:
    """Tests for the categorization chain."""
    
    def test_chain_with_rule_match(self, sample_transaction):
        """Test that chain returns result when rule matches."""
        keyword_strategy = KeywordStrategy()
        keyword_strategy.add_keyword("starbucks", str(uuid4()))
        
        chain = CategorizationChainBuilder() \
            .with_keyword_strategy(keyword_strategy) \
            .build()
        
        result = chain.handle(sample_transaction)
        
        assert result is not None
        assert result.method == "keyword_rule"
    
    def test_chain_falls_through_to_manual(self, sample_transaction):
        """Test that chain falls through to manual when no match."""
        # Empty strategies - no matches possible
        chain = CategorizationChainBuilder().build()
        
        result = chain.handle(sample_transaction)
        
        assert result.requires_manual == True


# ============================================================================
# OBSERVER PATTERN TESTS
# ============================================================================

class TestTransactionSubject:
    """Tests for the observer pattern."""
    
    def test_observer_notification(self, sample_transaction):
        """Test that observers are notified of transactions."""
        subject = TransactionSubject()
        
        # Track if observer was called
        notifications = []
        
        class MockObserver:
            def on_transaction_created(self, txn):
                notifications.append(txn)
            def on_transaction_updated(self, txn):
                pass
            def on_transaction_deleted(self, txn):
                pass
        
        observer = MockObserver()
        subject.attach(observer)
        subject.notify_created(sample_transaction)
        
        assert len(notifications) == 1
        assert notifications[0] == sample_transaction


# ============================================================================
# FACTORY PATTERN TESTS
# ============================================================================

class TestBudgetAlertFactory:
    """Tests for the alert factory."""
    
    def test_create_exceeded_alert(self, sample_budget):
        """Test creating an exceeded alert."""
        factory = BudgetAlertFactory()
        spending = Decimal("600.00")  # Over budget
        
        alert = factory.create_alert(sample_budget, spending, AlertType.EXCEEDED)
        
        assert alert is not None
        assert alert.alert_type == AlertType.EXCEEDED
        assert "exceeded" in alert.message.lower()
    
    def test_create_warning_alert(self, sample_budget):
        """Test creating a warning alert."""
        factory = BudgetAlertFactory()
        spending = Decimal("450.00")  # 90% of budget
        
        alert = factory.create_alert(sample_budget, spending, AlertType.WARNING_90)
        
        assert alert is not None
        assert alert.alert_type == AlertType.WARNING_90
        assert "90%" in alert.message


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestCategorizationIntegration:
    """Integration tests for the complete categorization flow."""
    
    def test_full_categorization_flow(self, sample_transaction):
        """Test the complete categorization flow."""
        # Setup strategies
        keyword_strategy = KeywordStrategy()
        coffee_category_id = str(uuid4())
        keyword_strategy.add_keyword("coffee", coffee_category_id)
        
        # Build chain
        chain = CategorizationChainBuilder() \
            .with_keyword_strategy(keyword_strategy) \
            .build()
        
        # Categorize
        result = chain.handle(sample_transaction)
        
        # Verify
        assert result.category_id == coffee_category_id
        assert result.confidence == ConfidenceLevel.HIGH


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
