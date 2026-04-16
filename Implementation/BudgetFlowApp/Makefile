# BudgetFlow development targets
# Run from BudgetFlowApp/ (repo root containing docker-compose.yml)

BACKEND := backend

# Resolve an absolute path to the venv python.
# $(CURDIR) may contain spaces, so we quote everywhere in recipes.
VENV_PYTHON := $(CURDIR)/$(BACKEND)/.venv/bin/python

# PYTHON is what migrate/test use.  Default to the venv python.
# Override with: make test PYTHON=python3.11
PYTHON ?= $(VENV_PYTHON)

# Postgres credentials (must match docker-compose.yml / .env)
POSTGRES_USER := budgetflow_user
POSTGRES_DB   := budgetflow_db

.PHONY: help venv install db-up db-down db-reset db-wait check-db migrate test all \
        seed-demo run-backend run-frontend \
        up down logs logs-backend logs-db logs-worker migrate-docker reset-docker ps health-check

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Local dev (backend runs on host, docker = DB only)
# ──────────────────────────────────────────────

venv: ## Create venv at backend/.venv (skip if exists)
	@test -d "$(CURDIR)/$(BACKEND)/.venv" || python3 -m venv "$(CURDIR)/$(BACKEND)/.venv"
	@echo "Venv ready."

install: venv ## Install backend deps into venv
	"$(VENV_PYTHON)" -m pip install --quiet --upgrade pip
	"$(VENV_PYTHON)" -m pip install --quiet -r $(BACKEND)/requirements.txt
	@echo "Dependencies installed."

db-up: ## Start Postgres container (detached)
	docker compose up -d db

db-down: ## Stop Postgres container
	docker compose down

db-reset: ## Drop volume, recreate DB, wait for healthy Postgres
	docker compose down -v
	docker compose up -d db
	@$(MAKE) db-wait

db-wait: ## Wait for Postgres healthcheck to pass
	@echo "Waiting for Postgres..."
	@until docker compose exec -T db pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB) >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres is ready."

check-db: ## Verify Postgres is running and accepting connections
	@docker compose ps -q db >/dev/null 2>&1 || { echo "Error: DB container is not running. Run: make db-up"; exit 1; }
	@docker compose exec -T db pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB) >/dev/null 2>&1 || { echo "Error: DB is not ready. Run: make db-up && make db-wait"; exit 1; }

migrate: check-db ## Run alembic upgrade head locally (requires DB)
	cd "$(CURDIR)/$(BACKEND)" && "$(PYTHON)" -m alembic upgrade head

test: check-db ## Run pytest -q (requires DB)
	cd "$(CURDIR)/$(BACKEND)" && "$(PYTHON)" -m pytest -q

run-backend: check-db ## Start backend locally (uvicorn on port 8000)
	cd "$(CURDIR)/$(BACKEND)" && "$(PYTHON)" -m uvicorn app.main:app --reload --port 8000

run-frontend: ## Start frontend (next dev on port 3000)
	cd "$(CURDIR)/frontend" && npm run dev

seed-demo: check-db ## Seed 3 demo accounts (idempotent; safe to re-run)
	cd "$(CURDIR)/$(BACKEND)" && "$(PYTHON)" -m app.scripts.seed_demo_data

all: db-reset install migrate test ## Full local reset: db-reset -> install -> migrate -> test

# ──────────────────────────────────────────────
# Docker dev (backend + db both in docker)
# ──────────────────────────────────────────────

up: ## Build and start all containers (db + backend)
	docker compose up -d --build

down: ## Stop all containers
	docker compose down

ps: ## Show container status
	docker compose ps

logs: ## Tail logs for all services
	docker compose logs -f

logs-backend: ## Tail backend container logs
	docker compose logs -f backend

logs-db: ## Tail db container logs
	docker compose logs -f db

logs-worker: ## Tail worker container logs
	docker compose logs -f worker

health-check: ## Check backend liveness and readiness endpoints
	@echo "GET /health"
	@curl -fsS http://localhost:8000/health || { echo "health check failed"; exit 1; }
	@echo
	@echo "GET /health/ready"
	@curl -fsS http://localhost:8000/health/ready || { echo "readiness check failed"; exit 1; }
	@echo

migrate-docker: ## Run alembic upgrade head inside backend container
	docker compose exec backend alembic upgrade head

reset-docker: ## Full docker reset: wipe volumes, rebuild, migrate
	docker compose down -v
	docker compose up -d --build
	@echo "Waiting for db healthcheck..."
	@until docker compose exec -T db pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB) >/dev/null 2>&1; do sleep 1; done
	docker compose exec backend alembic upgrade head
	@echo "Ready — backend on http://localhost:8000"
