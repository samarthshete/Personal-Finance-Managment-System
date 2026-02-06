# Design Patterns Implementation

## Intelligent Personal Finance Management System

---

## 1. Strategy Pattern

### Purpose
Define a family of algorithms (categorization rules), encapsulate each one, and make them interchangeable.

### Implementation

```python
from abc import ABC, abstractmethod

class CategorizationStrategy(ABC):
    """Strategy interface for categorization algorithms."""
    
    @abstractmethod
    def categorize(self, transaction: Transaction) -> CategoryResult:
        pass

class KeywordStrategy(CategorizationStrategy):
    """Categorize based on keyword matching."""
    
    def __init__(self):
        self.keywords = {}  # keyword -> category mapping
    
    def categorize(self, transaction: Transaction) -> CategoryResult:
        description = transaction.description.lower()
        for keyword, category in self.keywords.items():
            if keyword in description:
                return CategoryResult(category, ConfidenceLevel.HIGH)
        return None

class MerchantStrategy(CategorizationStrategy):
    """Categorize based on merchant name."""
    
    def categorize(self, transaction: Transaction) -> CategoryResult:
        # Match merchant to known categories
        pass

class LLMStrategy(CategorizationStrategy):
    """Categorize using AI/LLM."""
    
    def __init__(self, llm_adapter: LLMAdapter):
        self.llm_adapter = llm_adapter
    
    def categorize(self, transaction: Transaction) -> CategoryResult:
        prediction = self.llm_adapter.classify(transaction.description)
        return CategoryResult(prediction.category, ConfidenceLevel.MEDIUM)
```

### UML Reference
See `/UML/class_diagram.puml` - "Strategy Pattern" package

---

## 2. Observer Pattern

### Purpose
Define a one-to-many dependency so that when one object changes state (transaction created), all dependents (budget observers) are notified.

### Implementation

```python
from abc import ABC, abstractmethod
from typing import List

class TransactionObserver(ABC):
    """Observer interface for transaction events."""
    
    @abstractmethod
    def on_transaction_created(self, transaction: Transaction) -> None:
        pass

class TransactionSubject:
    """Subject that notifies observers of transaction events."""
    
    def __init__(self):
        self._observers: List[TransactionObserver] = []
    
    def attach(self, observer: TransactionObserver) -> None:
        self._observers.append(observer)
    
    def detach(self, observer: TransactionObserver) -> None:
        self._observers.remove(observer)
    
    def notify(self, transaction: Transaction) -> None:
        for observer in self._observers:
            observer.on_transaction_created(transaction)

class BudgetAlertObserver(TransactionObserver):
    """Observes transactions and checks budget thresholds."""
    
    def __init__(self, budget_repo: BudgetRepository, alert_factory: AlertFactory):
        self.budget_repo = budget_repo
        self.alert_factory = alert_factory
    
    def on_transaction_created(self, transaction: Transaction) -> None:
        budget = self.budget_repo.find_by_category(transaction.category_id)
        if budget:
            spending = self._calculate_spending(budget)
            if spending >= budget.amount * budget.alert_threshold:
                alert = self.alert_factory.create_alert(budget, spending)
                self._send_alert(alert)
```

### UML Reference
See `/UML/communication_diagram.puml` and `/UML/sequence_diagram.puml`

---

## 3. Chain of Responsibility Pattern

### Purpose
Avoid coupling the sender of a request to its receiver by giving more than one object a chance to handle the request. Chain the receiving objects.

### Implementation

```python
from abc import ABC, abstractmethod

class CategorizationHandler(ABC):
    """Handler in the categorization chain."""
    
    def __init__(self):
        self._next_handler: CategorizationHandler = None
    
    def set_next(self, handler: 'CategorizationHandler') -> 'CategorizationHandler':
        self._next_handler = handler
        return handler
    
    def handle(self, transaction: Transaction) -> CategoryResult:
        result = self.process(transaction)
        if result:
            return result
        if self._next_handler:
            return self._next_handler.handle(transaction)
        return CategoryResult(Category.UNCATEGORIZED, ConfidenceLevel.LOW)
    
    @abstractmethod
    def process(self, transaction: Transaction) -> CategoryResult:
        pass

class RuleBasedHandler(CategorizationHandler):
    """First handler: try rule-based categorization."""
    
    def __init__(self, strategies: List[CategorizationStrategy]):
        super().__init__()
        self.strategies = strategies
    
    def process(self, transaction: Transaction) -> CategoryResult:
        for strategy in self.strategies:
            result = strategy.categorize(transaction)
            if result:
                return result
        return None

class AIHandler(CategorizationHandler):
    """Second handler: try AI categorization."""
    
    def __init__(self, llm_strategy: LLMStrategy):
        super().__init__()
        self.llm_strategy = llm_strategy
    
    def process(self, transaction: Transaction) -> CategoryResult:
        result = self.llm_strategy.categorize(transaction)
        if result and result.confidence >= 0.7:
            return result
        return None

class ManualHandler(CategorizationHandler):
    """Last handler: require manual input."""
    
    def process(self, transaction: Transaction) -> CategoryResult:
        # Signal that manual input is required
        return CategoryResult(None, ConfidenceLevel.LOW, requires_manual=True)

# Usage: Build the chain
rule_handler = RuleBasedHandler([KeywordStrategy(), MerchantStrategy()])
ai_handler = AIHandler(LLMStrategy(openai_adapter))
manual_handler = ManualHandler()

rule_handler.set_next(ai_handler).set_next(manual_handler)

# Process transaction through chain
result = rule_handler.handle(transaction)
```

### UML Reference
See `/UML/activity_diagram.puml` and `/UML/sequence_diagram.puml`

---

## 4. State Pattern

### Purpose
Allow an object to alter its behavior when its internal state changes.

### Implementation

```python
from abc import ABC, abstractmethod

class AccountState(ABC):
    """State interface for account states."""
    
    @abstractmethod
    def deposit(self, account: 'Account', amount: Decimal) -> None:
        pass
    
    @abstractmethod
    def withdraw(self, account: 'Account', amount: Decimal) -> None:
        pass
    
    @abstractmethod
    def freeze(self, account: 'Account') -> None:
        pass

class ActiveState(AccountState):
    """Account is active - all operations allowed."""
    
    def deposit(self, account: 'Account', amount: Decimal) -> None:
        account.balance += amount
    
    def withdraw(self, account: 'Account', amount: Decimal) -> None:
        if account.balance >= amount:
            account.balance -= amount
        else:
            account.state = OverdrawnState()
            account.balance -= amount
    
    def freeze(self, account: 'Account') -> None:
        account.state = FrozenState()

class FrozenState(AccountState):
    """Account is frozen - no operations allowed."""
    
    def deposit(self, account: 'Account', amount: Decimal) -> None:
        raise AccountFrozenError("Cannot deposit to frozen account")
    
    def withdraw(self, account: 'Account', amount: Decimal) -> None:
        raise AccountFrozenError("Cannot withdraw from frozen account")
    
    def freeze(self, account: 'Account') -> None:
        pass  # Already frozen

class Account:
    """Context class that uses state."""
    
    def __init__(self):
        self.balance = Decimal(0)
        self.state: AccountState = ActiveState()
    
    def deposit(self, amount: Decimal) -> None:
        self.state.deposit(self, amount)
    
    def withdraw(self, amount: Decimal) -> None:
        self.state.withdraw(self, amount)
```

### UML Reference
See `/UML/state_diagram.puml`

---

## 5. Factory Method Pattern

### Purpose
Define an interface for creating an object, but let subclasses decide which class to instantiate.

### Implementation

```python
from abc import ABC, abstractmethod

class AlertFactory(ABC):
    """Factory interface for creating alerts."""
    
    @abstractmethod
    def create_alert(self, budget: Budget, spending: Decimal) -> BudgetAlert:
        pass

class BudgetAlertFactory(AlertFactory):
    """Concrete factory for budget alerts."""
    
    def create_alert(self, budget: Budget, spending: Decimal) -> BudgetAlert:
        percentage = spending / budget.amount
        
        if percentage >= 1.0:
            return self._create_exceeded_alert(budget, spending)
        elif percentage >= 0.9:
            return self._create_warning_alert(budget, spending, "90%")
        elif percentage >= 0.8:
            return self._create_warning_alert(budget, spending, "80%")
    
    def _create_exceeded_alert(self, budget: Budget, spending: Decimal) -> BudgetAlert:
        return BudgetAlert(
            alert_type=AlertType.EXCEEDED,
            message=f"Budget exceeded! Spent ${spending} of ${budget.amount}",
            budget_id=budget.budget_id,
            current_spending=spending
        )
    
    def _create_warning_alert(self, budget: Budget, spending: Decimal, level: str) -> BudgetAlert:
        return BudgetAlert(
            alert_type=AlertType.WARNING,
            message=f"Budget warning: {level} of budget used",
            budget_id=budget.budget_id,
            current_spending=spending
        )
```

### UML Reference
See `/UML/class_diagram.puml` - "Factory Pattern" package

---

## 6. Adapter Pattern

### Purpose
Convert the interface of a class into another interface clients expect.

### Implementation

```python
from abc import ABC, abstractmethod

class BankAdapter(ABC):
    """Adapter interface for bank integrations."""
    
    @abstractmethod
    def fetch_transactions(self, account_id: str, date_range: DateRange) -> List[Transaction]:
        pass

class PlaidAdapter(BankAdapter):
    """Adapter for Plaid API."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = PlaidClient(api_key)
    
    def fetch_transactions(self, account_id: str, date_range: DateRange) -> List[Transaction]:
        # Call Plaid API
        plaid_response = self.client.get_transactions(account_id, date_range)
        
        # Convert Plaid format to our Transaction format
        return [self._convert(t) for t in plaid_response.transactions]
    
    def _convert(self, plaid_txn) -> Transaction:
        return Transaction(
            amount=Decimal(plaid_txn['amount']),
            description=plaid_txn['name'],
            merchant_name=plaid_txn.get('merchant_name'),
            transaction_date=datetime.fromisoformat(plaid_txn['date'])
        )

class CSVAdapter(BankAdapter):
    """Adapter for CSV file imports."""
    
    def __init__(self, column_mapping: dict):
        self.column_mapping = column_mapping
    
    def fetch_transactions(self, file_path: str, date_range: DateRange = None) -> List[Transaction]:
        transactions = []
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                transactions.append(self._convert(row))
        return transactions
```

### UML Reference
See `/UML/component_diagram.puml` and `/UML/sequence_diagram.puml`

---

## 7. Repository Pattern

### Purpose
Mediate between the domain and data mapping layers using a collection-like interface for accessing domain objects.

### Implementation

```python
from abc import ABC, abstractmethod

class TransactionRepository(ABC):
    """Repository interface for Transaction entities."""
    
    @abstractmethod
    def find_by_id(self, transaction_id: UUID) -> Transaction:
        pass
    
    @abstractmethod
    def find_by_account(self, account_id: UUID) -> List[Transaction]:
        pass
    
    @abstractmethod
    def find_by_category(self, category_id: UUID) -> List[Transaction]:
        pass
    
    @abstractmethod
    def find_by_date_range(self, start: datetime, end: datetime) -> List[Transaction]:
        pass
    
    @abstractmethod
    def save(self, transaction: Transaction) -> Transaction:
        pass
    
    @abstractmethod
    def delete(self, transaction: Transaction) -> None:
        pass

class SQLAlchemyTransactionRepository(TransactionRepository):
    """SQLAlchemy implementation of TransactionRepository."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def find_by_id(self, transaction_id: UUID) -> Transaction:
        return self.session.query(TransactionModel).filter_by(id=transaction_id).first()
    
    def save(self, transaction: Transaction) -> Transaction:
        model = TransactionModel.from_entity(transaction)
        self.session.add(model)
        self.session.commit()
        return model.to_entity()
```

### UML Reference
See `/UML/class_diagram.puml` - "Repository Pattern" package

---

## Summary: Pattern Usage by Use Case

| Use Case | Patterns Used |
|----------|---------------|
| Import Transactions | Adapter, Factory, Observer |
| Categorize Expenses | Chain of Responsibility, Strategy |
| Set Budget Thresholds | Observer |
| Manage Accounts | State, Repository |
| Receive Budget Alerts | Observer, Factory |
| Export Reports | Factory |
