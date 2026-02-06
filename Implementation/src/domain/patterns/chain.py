"""
Chain of Responsibility Pattern Implementation
Categorization Handler Chain

Passes the transaction along a chain of handlers until one
successfully categorizes it: Rules -> AI -> Manual
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from src.domain.entities.models import Transaction, ConfidenceLevel
from src.domain.patterns.strategy import (
    CategorizationStrategy, CategoryResult, LLMStrategy
)


class CategorizationHandler(ABC):
    """
    Handler Interface
    
    Abstract base class for the categorization chain.
    Each handler attempts to categorize, then passes to next if unsuccessful.
    """
    
    def __init__(self):
        self._next_handler: Optional['CategorizationHandler'] = None
    
    def set_next(self, handler: 'CategorizationHandler') -> 'CategorizationHandler':
        """
        Set the next handler in the chain.
        
        Returns the handler to allow chaining: h1.set_next(h2).set_next(h3)
        """
        self._next_handler = handler
        return handler
    
    def handle(self, transaction: Transaction) -> CategoryResult:
        """
        Handle the categorization request.
        
        First attempts own processing, then delegates to next handler.
        """
        result = self.process(transaction)
        
        if result and result.category_id:
            return result
        
        if self._next_handler:
            return self._next_handler.handle(transaction)
        
        # End of chain - return uncategorized result
        return CategoryResult(
            category_id=None,
            confidence=ConfidenceLevel.LOW,
            method="none",
            requires_manual=True
        )
    
    @abstractmethod
    def process(self, transaction: Transaction) -> Optional[CategoryResult]:
        """
        Process the transaction.
        
        Implemented by concrete handlers.
        Returns CategoryResult if successful, None to pass to next handler.
        """
        pass


class RuleBasedHandler(CategorizationHandler):
    """
    Concrete Handler: Rule-based categorization
    
    First handler in the chain. Tries multiple rule-based strategies
    (keyword, merchant, amount) before passing to next handler.
    """
    
    def __init__(self, strategies: List[CategorizationStrategy] = None):
        super().__init__()
        self.strategies = strategies or []
    
    def add_strategy(self, strategy: CategorizationStrategy) -> None:
        """Add a categorization strategy."""
        self.strategies.append(strategy)
    
    def process(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Try each strategy in order."""
        for strategy in self.strategies:
            result = strategy.categorize(transaction)
            if result and result.category_id:
                return result
        return None


class AIHandler(CategorizationHandler):
    """
    Concrete Handler: AI/LLM-based categorization
    
    Second handler in the chain. Uses AI to categorize when rules fail.
    """
    
    def __init__(self, llm_strategy: LLMStrategy = None):
        super().__init__()
        self.llm_strategy = llm_strategy
    
    def set_llm_strategy(self, strategy: LLMStrategy) -> None:
        """Set the LLM strategy."""
        self.llm_strategy = strategy
    
    def process(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Use AI to categorize."""
        if self.llm_strategy:
            result = self.llm_strategy.categorize(transaction)
            if result and result.category_id:
                return result
        return None


class ManualHandler(CategorizationHandler):
    """
    Concrete Handler: Manual categorization
    
    Last handler in the chain. Signals that manual input is required.
    """
    
    def process(self, transaction: Transaction) -> Optional[CategoryResult]:
        """Return result indicating manual categorization needed."""
        return CategoryResult(
            category_id=None,
            confidence=ConfidenceLevel.LOW,
            method="manual_required",
            requires_manual=True
        )


# ============================================================================
# Chain Builder Helper
# ============================================================================

class CategorizationChainBuilder:
    """
    Builder for constructing the categorization chain.
    
    Usage:
        chain = CategorizationChainBuilder()
            .with_keyword_strategy(keyword_strategy)
            .with_merchant_strategy(merchant_strategy)
            .with_llm_strategy(llm_strategy)
            .build()
    """
    
    def __init__(self):
        self._rule_strategies: List[CategorizationStrategy] = []
        self._llm_strategy: Optional[LLMStrategy] = None
    
    def with_keyword_strategy(self, strategy) -> 'CategorizationChainBuilder':
        """Add keyword strategy."""
        self._rule_strategies.append(strategy)
        return self
    
    def with_merchant_strategy(self, strategy) -> 'CategorizationChainBuilder':
        """Add merchant strategy."""
        self._rule_strategies.append(strategy)
        return self
    
    def with_amount_strategy(self, strategy) -> 'CategorizationChainBuilder':
        """Add amount range strategy."""
        self._rule_strategies.append(strategy)
        return self
    
    def with_llm_strategy(self, strategy: LLMStrategy) -> 'CategorizationChainBuilder':
        """Set LLM strategy."""
        self._llm_strategy = strategy
        return self
    
    def build(self) -> CategorizationHandler:
        """
        Build and return the categorization chain.
        
        Chain: RuleBasedHandler -> AIHandler -> ManualHandler
        """
        # Create handlers
        rule_handler = RuleBasedHandler(self._rule_strategies)
        ai_handler = AIHandler(self._llm_strategy)
        manual_handler = ManualHandler()
        
        # Build chain
        rule_handler.set_next(ai_handler).set_next(manual_handler)
        
        return rule_handler
