# System Architecture

## Intelligent Personal Finance Management System

---

## 1. Architecture Overview

### 1.1 Architecture Style
**Layered Architecture** with 4 distinct layers:

```
┌─────────────────────────────────────────────────────────────┐
│                   PRESENTATION LAYER                        │
│              (Web App, Mobile App, API Gateway)             │
├─────────────────────────────────────────────────────────────┤
│                   APPLICATION LAYER                         │
│         (Services: Transaction, Budget, Analytics)          │
├─────────────────────────────────────────────────────────────┤
│                     DOMAIN LAYER                            │
│     (Entities, Business Rules, Design Patterns)             │
├─────────────────────────────────────────────────────────────┤
│                  INFRASTRUCTURE LAYER                       │
│        (Repositories, ORM, External Adapters)               │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  PostgreSQL  │  Redis   │
              └─────────────────────────┘
```

---

## 2. Layer Details

### 2.1 Presentation Layer

**Components:**
- Web Application (React.js)
- Mobile Application (Flutter)
- API Gateway (REST endpoints)

**Responsibilities:**
- User interface rendering
- Input validation
- Request routing
- Response formatting

### 2.2 Application Layer

**Components:**
- TransactionService
- BudgetService
- AccountService
- AnalyticsService
- NotificationService
- AuthService

**Responsibilities:**
- Orchestrate use cases
- Coordinate domain objects
- Handle transactions
- Enforce business rules

### 2.3 Domain Layer

**Components:**
- Entities (User, Account, Transaction, etc.)
- Value Objects (Money, DateRange)
- Domain Services (CategorizationEngine)
- Design Pattern implementations

**Responsibilities:**
- Core business logic
- Entity behavior
- Business rule enforcement
- Domain events

### 2.4 Infrastructure Layer

**Components:**
- Repositories (SQLAlchemy implementations)
- External Adapters (Plaid, OpenAI)
- ORM Layer
- Caching Layer (Redis)

**Responsibilities:**
- Data persistence
- External service integration
- Technical concerns

---

## 3. Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           PRESENTATION                                    │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────────────────┐│
│  │  Web App    │   │ Mobile App  │   │          API Gateway            ││
│  │  (React)    │   │  (Flutter)  │   │  /api/transactions              ││
│  └──────┬──────┘   └──────┬──────┘   │  /api/budgets                   ││
│         │                 │          │  /api/accounts                   ││
│         └────────┬────────┘          │  /api/analytics                  ││
│                  │                   └───────────────┬──────────────────┘│
└──────────────────┼───────────────────────────────────┼───────────────────┘
                   │                                   │
┌──────────────────┼───────────────────────────────────┼───────────────────┐
│                  ▼           APPLICATION             ▼                    │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────┐ │
│  │ TransactionService  │  │    BudgetService    │  │ AnalyticsService │ │
│  │ - importTransactions│  │ - createBudget      │  │ - getSpendingBy  │ │
│  │ - categorize        │  │ - checkThresholds   │  │ - getTrends      │ │
│  └──────────┬──────────┘  └──────────┬──────────┘  └────────┬─────────┘ │
└─────────────┼────────────────────────┼──────────────────────┼───────────┘
              │                        │                      │
┌─────────────┼────────────────────────┼──────────────────────┼───────────┐
│             ▼          DOMAIN        ▼                      ▼            │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │                    CategorizationEngine                             ││
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 ││
│  │  │RuleHandler  │─▶│ AIHandler   │─▶│ManualHandler│                 ││
│  │  └─────────────┘  └─────────────┘  └─────────────┘                 ││
│  └─────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │  BudgetObserver │  │  AlertFactory   │  │    Entities     │         │
│  │  (Observer)     │  │  (Factory)      │  │  User,Account   │         │
│  └─────────────────┘  └─────────────────┘  │  Transaction    │         │
│                                            └─────────────────┘         │
└────────────────────────────────────────────────────────────────────────┘
              │                        │                      │
┌─────────────┼────────────────────────┼──────────────────────┼───────────┐
│             ▼      INFRASTRUCTURE    ▼                      ▼            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │TransactionRepo  │  │  BudgetRepo     │  │  AccountRepo    │         │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘         │
│           │                    │                    │                   │
│           └────────────────────┼────────────────────┘                   │
│                                ▼                                        │
│                    ┌─────────────────────┐                              │
│                    │   ORM (SQLAlchemy)  │                              │
│                    └──────────┬──────────┘                              │
│  ┌─────────────┐              │              ┌─────────────┐            │
│  │PlaidAdapter │              │              │OpenAIAdapter│            │
│  └─────────────┘              │              └─────────────┘            │
└───────────────────────────────┼─────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │      PostgreSQL       │
                    │        Redis          │
                    └───────────────────────┘
```

---

## 4. Data Flow

### 4.1 Transaction Import Flow

```
User → API Gateway → TransactionService → BankAdapter (Plaid)
                                        ↓
                              CategorizationEngine
                              (Chain of Responsibility)
                                        ↓
                              TransactionRepository
                                        ↓
                              BudgetObserver (notify)
                                        ↓
                              AlertFactory (if threshold exceeded)
                                        ↓
                              NotificationService → User
```

### 4.2 Budget Check Flow

```
Transaction Created → TransactionSubject.notify()
                              ↓
                    BudgetAlertObserver.onTransactionCreated()
                              ↓
                    BudgetRepository.findByCategory()
                              ↓
                    Budget.checkThreshold()
                              ↓
                    AlertFactory.createAlert()
                              ↓
                    NotificationService.send()
```

---

## 5. Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend Web | React.js, TypeScript |
| Frontend Mobile | Flutter, Dart |
| API Gateway | Flask / FastAPI |
| Backend Language | Python 3.10+ |
| ORM | SQLAlchemy |
| Database | PostgreSQL 14+ |
| Cache | Redis |
| External - Bank | Plaid API |
| External - AI | OpenAI API / Anthropic API |
| Notifications | SendGrid (email), Firebase (push) |

---

## 6. Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         CLOUD                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Web App   │  │  Mobile App │  │   API       │        │
│  │   (CDN)     │  │ (App Store) │  │  Servers    │        │
│  └─────────────┘  └─────────────┘  └──────┬──────┘        │
│                                           │                │
│                                    ┌──────┴──────┐        │
│                                    │Load Balancer│        │
│                                    └──────┬──────┘        │
│                         ┌─────────────────┼─────────────┐ │
│                         │                 │             │ │
│                    ┌────┴────┐      ┌────┴────┐       │ │
│                    │ App     │      │ App     │       │ │
│                    │Server 1 │      │Server 2 │       │ │
│                    └────┬────┘      └────┬────┘       │ │
│                         │                │             │ │
│                         └────────┬───────┘             │ │
│                                  │                     │ │
│  ┌───────────────────────────────┼───────────────────┐│ │
│  │                               │                   ││ │
│  │  ┌──────────────┐    ┌───────┴────────┐         ││ │
│  │  │  PostgreSQL  │    │     Redis      │         ││ │
│  │  │  (Primary)   │    │    (Cache)     │         ││ │
│  │  └──────────────┘    └────────────────┘         ││ │
│  │                                                  ││ │
│  │  DATABASE LAYER                                  ││ │
│  └──────────────────────────────────────────────────┘│ │
└─────────────────────────────────────────────────────────┘
```

---

## 7. Security Architecture

### Authentication
- JWT tokens for API authentication
- bcrypt for password hashing
- Session expiration after 24 hours

### Authorization
- Role-based access control
- Resource-level permissions
- API rate limiting

### Data Protection
- HTTPS for all communications
- Encryption at rest (database)
- PII data masking in logs

---

## 8. SOLID Principles Mapping

| Principle | Implementation |
|-----------|----------------|
| **S** - Single Responsibility | TransactionService only handles transactions |
| **O** - Open/Closed | Strategy pattern allows new rules without modification |
| **L** - Liskov Substitution | All BankAdapters are interchangeable |
| **I** - Interface Segregation | Separate Repository interfaces per entity |
| **D** - Dependency Inversion | Services depend on Repository interfaces |
