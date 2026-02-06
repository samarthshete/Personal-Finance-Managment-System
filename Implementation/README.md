# Implementation

## Intelligent Personal Finance Management System

### CSCI 6234 - Object-Oriented Design

Note: This project is currently under development and not yet fully built.

---

## 📁 Project Structure

```
Implementation/
├── README.md
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── domain/
│   │   ├── entities/
│   │   ├── services/
│   │   └── patterns/
│   ├── application/
│   │   └── services/
│   ├── infrastructure/
│   │   ├── repositories/
│   │   └── adapters/
│   └── presentation/
│       └── api/
└── tests/
    └── test_*.py
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis (optional, for caching)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd Implementation

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Set up database
python -m src.infrastructure.database.init_db

# Run the application
python -m src.main
```

---

## 🧪 Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_categorization.py
```

---

## 📦 Dependencies

See `requirements.txt` for full list.

Key dependencies:

- Flask / FastAPI - Web framework
- SQLAlchemy - ORM
- OpenAI - LLM integration
- pytest - Testing

---

## 🎯 Design Patterns Implemented

| Pattern                 | Location                           |
| ----------------------- | ---------------------------------- |
| Strategy                | `src/domain/patterns/strategy/`    |
| Observer                | `src/domain/patterns/observer/`    |
| Chain of Responsibility | `src/domain/patterns/chain/`       |
| State                   | `src/domain/patterns/state/`       |
| Factory                 | `src/domain/patterns/factory/`     |
| Adapter                 | `src/infrastructure/adapters/`     |
| Repository              | `src/infrastructure/repositories/` |
