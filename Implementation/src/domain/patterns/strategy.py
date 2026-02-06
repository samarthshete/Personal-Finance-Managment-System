"""
Strategy Pattern Implementation
Categorization Strategies

Defines a family of algorithms for transaction categorization,
encapsulates each one, and makes them interchangeable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, List
from decimal import Decimal

from src.domain.entities.models import (
    Transaction, Category, ConfidenceLevel, CategorizationRule, RuleType
)


@dataclass
class CategoryResult:
    """Result of categorization attempt."""
    category_id: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    method: str = ""
    requires_manual: bool = False


class CategorizationStrategy(ABC):
    """
    Strategy Interface
    
    Defines the interface for categorization algorithms.
    Each concrete strategy implements a different approach.
    """
    
    @abstractmethod
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """
        Attempt to categorize a transaction.
        
        Args:
            transaction: The transaction to categorize
            
        Returns:
            CategoryResult if successful, None if no match
        """
        pass


class KeywordStrategy(CategorizationStrategy):
    """
    Concrete Strategy: Keyword-based categorization
    
    Matches transaction descriptions against keyword patterns.
    """
    
    def __init__(self):
        self.keywords: Dict[str, str] = {}  # keyword -> category_id
    
    def add_keyword(self, keyword: str, category_id: str) -> None:
        """Add a keyword-category mapping."""
        self.keywords[keyword.lower()] = category_id
    
    def load_rules(self, rules: List[CategorizationRule]) -> None:
        """Load rules from database."""
        for rule in rules:
            if rule.rule_type == RuleType.KEYWORD and rule.is_active:
                self.keywords[rule.pattern.lower()] = str(rule.category_id)
    
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Match transaction description against keywords."""
        description = transaction.description.lower()
        
        for keyword, category_id in self.keywords.items():
            if keyword in description:
                return CategoryResult(
                    category_id=category_id,
                    confidence=ConfidenceLevel.HIGH,
                    method="keyword_rule"
                )
        return None


class MerchantStrategy(CategorizationStrategy):
    """
    Concrete Strategy: Merchant-based categorization
    
    Matches transaction merchant names against known merchants.
    """
    
    def __init__(self):
        self.merchants: Dict[str, str] = {}  # merchant -> category_id
    
    def add_merchant(self, merchant: str, category_id: str) -> None:
        """Add a merchant-category mapping."""
        self.merchants[merchant.lower()] = category_id
    
    def load_rules(self, rules: List[CategorizationRule]) -> None:
        """Load rules from database."""
        for rule in rules:
            if rule.rule_type == RuleType.MERCHANT and rule.is_active:
                self.merchants[rule.pattern.lower()] = str(rule.category_id)
    
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Match transaction merchant against known merchants."""
        merchant = transaction.merchant_name.lower()
        
        for known_merchant, category_id in self.merchants.items():
            if known_merchant in merchant:
                return CategoryResult(
                    category_id=category_id,
                    confidence=ConfidenceLevel.HIGH,
                    method="merchant_rule"
                )
        return None


class AmountRangeStrategy(CategorizationStrategy):
    """
    Concrete Strategy: Amount-based categorization
    
    Categorizes based on transaction amount ranges.
    """
    
    def __init__(self):
        self.ranges: List[tuple] = []  # (min, max, category_id)
    
    def add_range(self, min_amount: Decimal, max_amount: Decimal, category_id: str) -> None:
        """Add an amount range-category mapping."""
        self.ranges.append((min_amount, max_amount, category_id))
    
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Match transaction amount against ranges."""
        amount = abs(transaction.amount)
        
        for min_amt, max_amt, category_id in self.ranges:
            if min_amt <= amount <= max_amt:
                return CategoryResult(
                    category_id=category_id,
                    confidence=ConfidenceLevel.MEDIUM,
                    method="amount_rule"
                )
        return None


class LLMStrategy(CategorizationStrategy):
    """
    Concrete Strategy: AI/LLM-based categorization
    
    Uses Large Language Model to classify transactions.
    Requires an LLM adapter (Adapter Pattern).
    """
    
    def __init__(self, llm_adapter=None):
        self.llm_adapter = llm_adapter
        self.confidence_threshold = Decimal("0.7")
    
    def set_adapter(self, adapter) -> None:
        """Set the LLM adapter."""
        self.llm_adapter = adapter
    
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Use LLM to classify transaction."""
        if not self.llm_adapter:
            return None
        
        try:
            prediction = self.llm_adapter.classify(
                text=transaction.description,
                merchant=transaction.merchant_name
            )
            
            if prediction and prediction.confidence >= self.confidence_threshold:
                return CategoryResult(
                    category_id=prediction.category_id,
                    confidence=ConfidenceLevel.MEDIUM,
                    method="llm"
                )
        except Exception as e:
            print(f"LLM categorization failed: {e}")
        
        return None


# ============================================================================
# Context Class (Uses Strategies)
# ============================================================================

class CategorizationContext:
    """
    Context class that uses Strategy pattern.
    
    Allows switching between different categorization strategies.
    """
    
    def __init__(self, strategy: CategorizationStrategy = None):
        self._strategy = strategy
    
    def set_strategy(self, strategy: CategorizationStrategy) -> None:
        """Set the categorization strategy."""
        self._strategy = strategy
    
    def categorize(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Execute the current strategy."""
        if self._strategy:
            return self._strategy.categorize(transaction)
        return None
