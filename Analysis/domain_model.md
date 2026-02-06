# Domain Model Analysis

## Intelligent Personal Finance Management System

---

## 1. Domain Overview

The Intelligent Personal Finance Management System operates in the personal finance domain, helping users track, categorize, and analyze their financial transactions while managing budgets.

---

## 2. Key Domain Entities

### 2.1 User
**Description:** A person who uses the system to manage finances.

| Attribute | Type | Description |
|-----------|------|-------------|
| userId | UUID | Unique identifier |
| email | String | Login credential |
| passwordHash | String | Encrypted password |
| name | String | Display name |
| createdAt | DateTime | Registration date |

**Responsibilities:**
- Authenticate with the system
- Own multiple accounts
- Create budgets and rules

---

### 2.2 Account
**Description:** A financial account (checking, savings, credit card, etc.)

| Attribute | Type | Description |
|-----------|------|-------------|
| accountId | UUID | Unique identifier |
| userId | UUID | Owner reference |
| name | String | Account name |
| accountType | Enum | CHECKING, SAVINGS, CREDIT_CARD, INVESTMENT |
| balance | Decimal | Current balance |
| state | Enum | ACTIVE, FROZEN, CLOSED |

**Responsibilities:**
- Track balance
- Contain transactions
- Manage state transitions

**State Machine:**
```
[*] → Pending → Active → Frozen → Closed → [*]
                  ↓
              Overdrawn
```

---

### 2.3 Transaction
**Description:** A single financial record (expense or income).

| Attribute | Type | Description |
|-----------|------|-------------|
| transactionId | UUID | Unique identifier |
| accountId | UUID | Parent account |
| amount | Decimal | Transaction amount |
| description | String | Transaction details |
| merchantName | String | Merchant/payee |
| categoryId | UUID | Assigned category |
| transactionDate | DateTime | When occurred |
| isRecurring | Boolean | Recurring flag |
| confidence | Enum | HIGH, MEDIUM, LOW, USER_VERIFIED |
| categorizationMethod | String | rule/ai/manual |

**Responsibilities:**
- Store financial data
- Be categorized
- Trigger budget checks

---

### 2.4 Category
**Description:** Classification for grouping transactions.

| Attribute | Type | Description |
|-----------|------|-------------|
| categoryId | UUID | Unique identifier |
| name | String | Category name |
| parentCategoryId | UUID | Parent category (hierarchical) |
| icon | String | Display icon |
| color | String | Display color |
| isSystem | Boolean | System vs user-defined |

**Responsibilities:**
- Organize transactions
- Support hierarchy (parent/child)
- Link to budgets

**Example Hierarchy:**
```
Food & Dining
├── Groceries
├── Restaurants
└── Coffee Shops

Transportation
├── Gas
├── Public Transit
└── Parking
```

---

### 2.5 Budget
**Description:** Spending limit for a category over a period.

| Attribute | Type | Description |
|-----------|------|-------------|
| budgetId | UUID | Unique identifier |
| userId | UUID | Owner reference |
| categoryId | UUID | Monitored category |
| amount | Decimal | Budget limit |
| period | Enum | WEEKLY, MONTHLY, YEARLY |
| alertThreshold | Decimal | Alert trigger (e.g., 0.8) |
| startDate | DateTime | Budget start |

**Responsibilities:**
- Define spending limits
- Track utilization
- Trigger alerts when exceeded

---

### 2.6 CategorizationRule
**Description:** Rule for automatic transaction categorization.

| Attribute | Type | Description |
|-----------|------|-------------|
| ruleId | UUID | Unique identifier |
| userId | UUID | Owner reference |
| ruleName | String | Rule name |
| ruleType | Enum | KEYWORD, MERCHANT, AMOUNT_RANGE |
| pattern | String | Match pattern |
| categoryId | UUID | Target category |
| priority | Integer | Execution order |
| isActive | Boolean | Active status |

**Responsibilities:**
- Match transactions
- Assign categories automatically
- Support multiple rule types

---

### 2.7 BudgetAlert
**Description:** Notification when budget threshold is reached.

| Attribute | Type | Description |
|-----------|------|-------------|
| alertId | UUID | Unique identifier |
| budgetId | UUID | Related budget |
| alertType | Enum | WARNING_80, WARNING_90, EXCEEDED |
| message | String | Alert message |
| currentSpending | Decimal | Current amount |
| budgetLimit | Decimal | Budget limit |
| createdAt | DateTime | Alert time |
| isRead | Boolean | Read status |

---

## 3. Entity Relationships

```
User (1) -------- (0..*) Account
User (1) -------- (0..*) Budget
User (1) -------- (0..*) CategorizationRule

Account (1) -------- (0..*) Transaction

Transaction (0..*) -------- (1) Category

Budget (0..*) -------- (1) Category

Category (0..*) -------- (0..1) Category [parent]

CategorizationRule (0..*) -------- (1) Category
```

---

## 4. BCE (Boundary-Control-Entity) Classification

### 4.1 Boundary Classes (Interface)
| Class | Purpose |
|-------|---------|
| WebApp | React frontend |
| MobileApp | Flutter mobile app |
| APIGateway | REST API interface |
| BankAdapter | External bank integration |
| LLMAdapter | AI service integration |
| NotificationService | Alert delivery |

### 4.2 Control Classes (Business Logic)
| Class | Purpose |
|-------|---------|
| TransactionService | Transaction operations |
| BudgetService | Budget management |
| AccountService | Account operations |
| CategorizationEngine | Categorization logic |
| AnalyticsService | Report generation |
| AuthService | Authentication |

### 4.3 Entity Classes (Data)
| Class | Purpose |
|-------|---------|
| User | User data |
| Account | Account data |
| Transaction | Transaction data |
| Category | Category data |
| Budget | Budget data |
| CategorizationRule | Rule data |
| BudgetAlert | Alert data |

---

## 5. Domain Rules

### Business Rules
1. A transaction must belong to exactly one account
2. A transaction must have exactly one category
3. A budget monitors one category (or overall spending)
4. Alert thresholds must be between 0 and 1 (0-100%)
5. Account balance is derived from transaction sum

### Constraints
1. User email must be unique
2. Transaction amount cannot be zero
3. Budget amount must be positive
4. Category names must be unique per user
5. Rule priorities must be unique per user

---

## 6. Glossary

| Term | Definition |
|------|------------|
| Categorization | Process of assigning a category to a transaction |
| Threshold | Percentage of budget that triggers an alert |
| Confidence | Certainty level of automatic categorization |
| Period | Time span for budget calculation |
| Recurring | Transaction that repeats regularly |
