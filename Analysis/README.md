# Analysis

## Intelligent Personal Finance Management System
### CSCI 6234 - Object-Oriented Design

---

## 📁 Documents

| Document | Description |
|----------|-------------|
| `domain_model.md` | Domain Model and Entity Analysis |
| `analysis_classes.md` | Analysis Class Diagrams |

---

## 🎯 Analysis Overview

This folder contains the analysis phase artifacts including:
- Domain model identification
- Entity-relationship analysis
- Boundary, Control, Entity (BCE) classification
- Key abstractions and responsibilities

---

## 📊 Domain Entities

| Entity | Description | Key Attributes |
|--------|-------------|----------------|
| User | System user | userId, email, name |
| Account | Financial account | accountId, name, balance, state |
| Transaction | Financial record | amount, description, date, category |
| Category | Transaction classification | name, parentCategory |
| Budget | Spending limit | amount, period, alertThreshold |
| CategorizationRule | Auto-categorization rule | pattern, ruleType, targetCategory |
| BudgetAlert | Threshold notification | alertType, message |
