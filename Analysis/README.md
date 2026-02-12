# Analysis - Robustness Diagrams

## Intelligent Personal Finance Management System
### CSCI 6234 - Object-Oriented Design

---

## 📁 Folder Contents

| File Pattern | Description |
|--------------|-------------|
| `UC##_<Name>.puml` | PlantUML source file |
| `UC##_<Name>.png` | Generated PNG image |
| `UC##_<Name>_HandDrawn.pdf` | Hand-drawn diagram (PDF) |

---

## 📊 Robustness Diagrams (7 Use Cases)

| UC# | Use Case Name | File |
|-----|---------------|------|
| UC01 | Authenticate User | `UC01_Authenticate_User.puml` |
| UC02 | Manage Accounts | `UC02_Manage_Accounts.puml` |
| UC03 | Import Transactions | `UC03_Import_Transactions.puml` |
| UC04 | Categorize Expenses | `UC04_Categorize_Expenses.puml` |
| UC05 | Set Budget Thresholds | `UC05_Set_Budget_Thresholds.puml` |
| UC06 | Receive Budget Alerts | `UC06_Receive_Budget_Alerts.puml` |
| UC07 | View Analytics Dashboard | `UC07_View_Analytics_Dashboard.puml` |

---

## 🔧 Robustness Diagram Elements

### Stereotypes Used

| Symbol | Type | Description |
|--------|------|-------------|
| 🖥️ | **Boundary** | UI screens, forms, interfaces |
| ⚙️ | **Control** | Controllers, handlers, business logic |
| 📦 | **Entity** | Domain objects, data entities |

### Connection Rules

```
✅ ALLOWED:
   Actor ←→ Boundary
   Boundary ←→ Control
   Control ←→ Control
   Control ←→ Entity

❌ NOT ALLOWED:
   Actor ←→ Control
   Actor ←→ Entity
   Boundary ←→ Entity
```

---

## 🎯 Design Patterns Identified

| Use Case | Design Patterns |
|----------|-----------------|
| UC04 - Categorize Expenses | Chain of Responsibility, Strategy, Adapter |
| UC06 - Receive Budget Alerts | Observer, Factory |
| UC02 - Manage Accounts | Repository |
| UC03 - Import Transactions | Adapter |

---

## 🔗 How to Generate PNG Images

### Option 1: PlantUML Online Server
1. Go to http://www.plantuml.com/plantuml/uml/
2. Paste `.puml` file content
3. Right-click image → Save as PNG

### Option 2: VS Code
1. Install PlantUML extension
2. Open `.puml` file
3. Press `Alt+D` to preview
4. Export as PNG

### Option 3: Command Line
```bash
java -jar plantuml.jar UC01_Authenticate_User.puml
```

---

## 📚 References

- I. Jacobson et al., *The Unified Software Development Process*, Addison Wesley, 1999
- J. Arlow and I. Neustadt, *UML and the Unified Process*, Addison Wesley, 2002
- https://gyires.inf.unideb.hu/GyBITT/07/ch03s05.html
